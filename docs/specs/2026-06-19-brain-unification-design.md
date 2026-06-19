# Brain Unification — Design Spec

**Date:** 2026-06-19
**Status:** Approved
**Author:** Devon + Claude (brainstorming session)

## 1. Problem & Goal

App Brain, Infra Brain, and Open Brain are three MCP servers that "don't behave the
same," making debugging and maintenance costly. A technical comparison showed they are
~80% identical but have drifted in specific, high-impact ways:

- **Framework split:** Infra Brain runs `fastmcp` 3.2.0 + FastAPI 0.135; App Brain and
  Open Brain run `fastmcp` 2.3.4 + FastAPI 0.115. Two MCP runtimes serving the "same"
  protocol.
- **Two domain-routing mechanisms:** Open Brain uses Coolify-managed
  `docker_compose_domains`; App Brain and Infra Brain hand-wire Traefik labels into
  compose (frozen, unmanaged, `fqdn` reads null).
- **DB image mismatch:** Infra Brain on `postgres:16-alpine`; App/Open on
  `pgvector/pgvector:pg16`.
- **Independent `start.sh`, auth, health, Dockerfile** per repo — copy-drift across three
  codebases.

**Goal:** one standardized technical deployment for all three brains, where the **only
per-brain difference is the database** (and a small set of env vars). One codebase, one
image definition, behavior selected at runtime.

## 2. Locked Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Runtime topology | **Multi-instance** — one image, deployed 3× (one container per brain) | Keeps brains fault-isolated; one brain crashing/OOM/bad-deploy never takes the others down. Critical because these are the knowledge backbone and the agent depends on them at runtime. |
| Source control | **New repo `AlobarQuest/brain`** | Structured for multi-brain from day one; no single brain's quirks become the base. Old three repos archived (not deleted). |
| FastMCP version | **`fastmcp>=3.4.2,<4`** | 3.x is the GA, actively-developed line; 2.x is maintenance-only. Standardizing on 2.x would buy a second forced migration later. Infra Brain is already on 3.x — it's the one ahead. |
| DB image | **`pgvector/pgvector:pg16` everywhere** | Harmless superset; Infra Brain simply never creates the vector extension. Makes the Postgres image identical across all three. |
| Domain routing | **Coolify `docker_compose_domains`** | The managed/supported path (Open Brain's approach); drop hand-wired Traefik labels. |
| Secrets | **Runtime-only** (drop build-time flag) | Current secrets are flagged build-time AND runtime — broader exposure than needed; Dockerfile doesn't consume them at build. |
| Health check | **Compose-level only**; disable Coolify's shadow HTTP check | Two disagreeing health configs today; pick one source of truth. |
| Cutover | **Repoint existing Coolify apps**, one at a time | Preserves each app's UUID → its existing `<UUID>_postgres-data` volume + data. Zero data migration. |
| Build artifact (Phase 1) | **Build-from-repo ×3** | Identical Dockerfile → identical images, zero new infra. |
| Build artifact (Phase 2) | **Build-once → GHCR → deploy by tag** | True single artifact + faster deploys. Runtime topology stays multi-instance/fault-isolated permanently. |

## 3. Architecture

One repo → one Docker image definition → deployed three times. A single env var
`BRAIN_TYPE` (`app` | `infra` | `open`) selects at startup: which toolset to register,
which DB to talk to, which migrations to run, and whether embeddings are enabled. Every
deployment runs byte-identical code; the only per-brain differences are **env vars + the
database**.

Shared core (written once): FastAPI host, FastMCP mount at `/mcp` (+ the no-trailing-slash
alias shim), `x-brain-key` auth middleware, `/api/health`, async DB engine, OpenRouter
embeddings client, Dockerfile, `start.sh`.

## 4. Repo Structure

```
brain/
  Dockerfile                 # multi-stage, py3.12-slim, non-root appuser, EXPOSE 80, CMD start.sh
  docker-compose.yml         # api + db (pgvector/pgvector:pg16); domain via Coolify docker_compose_domains
  docker-compose.local.yml   # local dev overrides
  requirements.txt           # fastmcp>=3.4.2,<4, fastapi, uvicorn[standard], sqlalchemy[async], asyncpg, pgvector, httpx, pydantic-settings, alembic
  requirements-dev.txt
  scripts/start.sh           # per-brain alembic upgrade → optional per-brain seed → uvicorn
  src/
    core/
      app.py                 # builds FastAPI, mounts FastMCP at /mcp, loads active brain via registry, registers /api/health
      config.py              # base Settings + BRAIN_TYPE enum + per-brain validation
      db.py                  # async engine factory (pool 10 / overflow 20 / pre_ping / recycle 3600)
      auth.py                # x-brain-key / ?key= middleware (hmac compare), configurable allowlist
      embeddings.py          # OpenRouter client; initialized only when active brain.capabilities.embeddings
      mcp_alias.py           # MCPPrefixAlias shim (/mcp ≡ /mcp/)
      registry.py            # BRAIN_TYPE -> brain module
    brains/
      app/
        __init__.py          # register(mcp); capabilities = {embeddings: True}
        tools.py             # App/AppKnowledge tools
        models.py            # App, AppKnowledge (Vector(1536))
        repositories.py
        migrations/          # relocated app-brain alembic versions (revision IDs preserved)
        alembic.ini
        seed.py              # optional; absent for app
      infra/
        __init__.py          # capabilities = {embeddings: False}
        tools.py             # rules / combos / lessons / versions
        models.py
        repositories.py
        migrations/          # relocated infra-brain alembic versions (preserved)
        alembic.ini
        seed.py              # seed/data.json loader (--skip-existing) — infra keeps its boot seed
      open/
        __init__.py          # capabilities = {embeddings: True}
        tools.py             # thought capture / semantic search
        models.py
        repositories.py
        migrations/          # relocated open-brain alembic versions (preserved)
        alembic.ini
  tests/
```

Each brain package owns only what is unique to it: tools, SQLAlchemy models, repositories,
its own Alembic migration tree, and an optional seed hook.

## 5. How `BRAIN_TYPE` Wires Everything

- **Tools:** `core/registry.py` maps `BRAIN_TYPE` → brain module; `app.py` calls
  `brain.register(mcp)`. Only the active brain's tools are registered.
- **Capabilities:** each brain declares flags (e.g. `embeddings`). `embeddings.py` /
  OpenRouter initializes only when `brain.capabilities.embeddings` is true — Infra Brain
  never touches OpenRouter though the dependency ships in the image.
- **Migrations:** `start.sh` runs `alembic -c src/brains/$BRAIN_TYPE/alembic.ini upgrade
  head` against the brain's own DB. **Existing migration files are relocated verbatim
  (revision IDs unchanged)** so each existing DB's `alembic_version` already matches and
  the first `upgrade head` is a clean no-op — no schema rewrite, no data touch.
- **Seed:** after migrations, `start.sh` runs `python -m src.brains.$BRAIN_TYPE.seed
  --skip-existing` if that module exists (only Infra Brain has one today).
- **Auth allowlist (per-brain):** the shared `auth.py` middleware reads its unauthenticated
  allowlist from the active brain's declaration. `infra`/`open` allow only `/api/health`;
  `app` additionally allows `/register` + `/.well-known/*` — this **intentionally** permits
  unauthenticated connections for App Brain and is preserved as-is (not changed by this build).
- **Config:** shared `Settings` (port, `MCP_ACCESS_KEY`, DB creds, log level) + per-brain
  extras validated only when relevant (`OPENROUTER_API_KEY` required for `app`/`open`,
  ignored for `infra`). `MCP_ACCESS_KEY` keeps the 64-char-hex validation.

## 6. Standardization Fixes (folded in by construction)

Because everything is shared core, the original drift disappears: one FastMCP pin (3.4.2),
one Dockerfile/`start.sh`, one auth implementation, `pgvector/pgvector:pg16` everywhere,
Coolify-managed domains (no hand-wired Traefik labels), runtime-only secrets, single
compose-level health-check source of truth.

## 7. Deployment & Cutover (zero data migration)

Repoint the three **existing** Coolify apps at the new `brain` repo (don't create new
ones) — preserves each app's UUID and therefore its `<UUID>_postgres-data` volume + data.

Per-brain config matrix:

| | app | infra | open |
|---|---|---|---|
| Coolify app UUID | `x8gkog0ow8k4oo80occ08g0w` | `hg8kkgo0kwoo8goswswgsko0` | `e0000okgowcgkw0wosgo8kg8` |
| `BRAIN_TYPE` | `app` | `infra` | `open` |
| DB user / db name (keep existing) | `appbrain` | `infrabrain` | `openbrain` |
| Embeddings / OpenRouter | yes | **no** | yes |
| Domain (via `docker_compose_domains`) | `app-brain.devonwatkins.com` | `infra-brain.devonwatkins.com` | `open-brain.devonwatkins.com` |
| DB image change | none | `postgres:16-alpine` → `pgvector/pgvector:pg16` (same PG16 major; PGDATA-compatible) | none |
| Existing `MCP_ACCESS_KEY` | keep | keep | keep |

Cutover sequence: one brain at a time → deploy → verify `/api/health` + an MCP tool call →
proceed to next. Rollback for a brain = repoint that one app back to its archived old repo.
Suggested order: **infra first** (no embeddings, simplest; also exercises the DB-image
swap), then **open**, then **app**.

## 8. Roadmap

- **Phase 1 (this spec):** build the `brain` repo; cut over all three via build-from-repo×3.
- **Phase 2:** build-once → push to GHCR → switch the three Coolify apps to
  `build_pack: dockerimage` on a shared tag. Runtime topology unchanged (stays
  multi-instance / fault-isolated).

## 9. Open Items / Risks

- **App Brain `/register` + `/.well-known/*` allowlist:** ~~Confirm whether intentional.~~
  **Resolved (2026-06-19):** intentional — App Brain purposely allows unauthenticated
  connections on these paths. Preserved unchanged; modeled as a per-brain configurable
  allowlist in shared `auth.py` (see §5). Not modified by this build.
- **`stateless_http=True` + sampling:** all three set stateless HTTP. Historically fragile
  with `ctx.sample()`/elicitation. Smoke-test any sampling calls on 3.4.2 before cutover.
- **Infra DB image swap:** `postgres:16-alpine` → `pgvector/pgvector:pg16` is same-major
  (PG16) so the on-disk data dir is compatible; verify on infra (cutover first) before the
  others.
- **FastMCP 3 migration sweep:** grep the relocated code for removed v2 kwargs
  (`message_path`, WS client transport, duplicate-handling params, auth env auto-load) that
  now hard-error in v3. App + Open Brain tools must move from `FastMCP(..., json_response,
  stateless_http)` to `http_app(..., json_response, stateless_http)`.
- **App Brain 502:** during investigation App Brain's MCP endpoint returned a transient 502
  while the container reported healthy. Watch for recurrence post-cutover; may be runtime/
  proxy-path related and could resolve with the unified 3.4.2 runtime.

## 10. Non-Goals

- Runtime topology B (single multi-tenant service) — explicitly **not** pursued; fault
  isolation is a permanent requirement.
- Migrating data between databases — each brain keeps its existing DB + volume.
- Changing tool semantics / APIs of any brain — this is a packaging/deployment
  unification, not a feature change.
