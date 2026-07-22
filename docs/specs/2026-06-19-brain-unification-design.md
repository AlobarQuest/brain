# Brain Unification — Design Spec

**Date:** 2026-06-19
**Status:** Revised — pending review (conformed to Flavor B)
**Author:** Devon + Claude (brainstorming session)

> **Revision note (2026-06-19):** The original spec standardized the three brains on
> their *common as-built state* (compose + embedded Postgres, port 80, Coolify-store
> secrets) without first consulting infra-brain. After loading the project standards and
> researching infra-brain's canonical sources, Devon chose to **fully conform to
> Flavor B**. This revision reflects that: a single-container **GHCR image** app +
> **separate Coolify-managed Postgres** per brain, **port 8000**, **BWS secrets**,
> **GitHub Actions CI/CD**, **Coolify FQDN domains**. The core application code is
> essentially unchanged from the original design; the changes are in packaging,
> database topology, secrets, CI/CD, and cutover (which now includes a one-time data
> migration). Decision rationale and the rejected alternatives are in §11.

## 1. Problem & Goal

App Brain, Infra Brain, and Open Brain are three MCP servers that "don't behave the
same," making debugging and maintenance costly. They are ~80% identical but drifted in
framework version, domain routing, DB image, and per-repo `start.sh`/auth/health/Dockerfile.

Two findings shaped the approach:
1. **They share ~80% of their architecture** — a strong shared template is achievable.
2. **They deviate from the documented Flavor-B standard** (and so does infra-brain itself,
   which is *labeled* B but built like A/C). Devon chose to make `brain` the app that
   actually implements Flavor B properly, gaining its real benefits (managed-DB backups,
   reproducible GHCR artifact, BWS secret hygiene).

**Goal:** one standardized **Flavor-B** deployment for all three brains, where the **only
per-brain difference is the database** (and a small set of env vars). One codebase, one
**GHCR image**, deployed three times; behavior selected at runtime by `BRAIN_TYPE`.

## 2. Locked Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Runtime topology | **Multi-instance** — one image, deployed 3× (one container per brain) | Fault isolation; one brain crashing never takes the others down. The brains are the knowledge backbone the agent depends on. |
| Source control | **New repo `AlobarQuest/brain`** (+ Bitbucket mirror) | Multi-brain from day one; old three repos archived. |
| Deployment flavor | **Flavor B** (single-container) | Devon's decision; gains managed-DB backups + GHCR artifact + BWS hygiene. |
| Packaging / build | **GHCR image** `ghcr.io/alobarquest/brain`, built & pushed by **GitHub Actions**; Coolify build pack `dockerimage` pulls it | Reproducible single artifact = the literal "one image deployed 3×" goal; no VPS source-build; fast deploys. |
| FastMCP version | **`fastmcp>=3.4.2,<4`** | 3.x is GA/active; the brains' code already uses the 3.x API (the served `2.3.4` pin is stale). |
| Port | **8000** | Documented Python/FastAPI standard; unprivileged; no special-casing. |
| Health check | **`GET /api/health`** (single-container standard), DB-aware → 200 `ok` / 503 `degraded` | Single-container apps use `/api/health`; it already behaves as a readiness probe. Coolify probe: host `127.0.0.1`, port `8000`, enabled, interval 10s, timeout 5s, retries 5, start_period 15s; exempt from auth. |
| Database | **Separate Coolify-managed Postgres resource per brain**, `pgvector/pgvector:pg16`, PG16, Coolify auto-backup enabled | Flavor B; gains automated backups (the brains have **none** today). One DB per brain (the only per-brain difference). |
| Domains | **Coolify FQDN field** (single-container), `https://<brain>.devonwatkins.com`, Let's Encrypt | Single-container standard; simpler than compose Traefik labels. |
| Secrets | **BWS** (Bitwarden Secrets Manager) — referenced by UUID, recorded in `.bws-secrets.toml`; values injected as Coolify env vars at deploy | Matches Devon's security posture + bws hooks; single source of truth, rotation, audit. Never committed. |
| Cutover | **Per brain: snapshot → create managed DB → restore → deploy GHCR app → verify → swap domain**, one at a time | Controlled, rollback-able. **Note: this introduces a one-time data migration** (embedded → managed Postgres). |
| Branch | `main` (triggers CI/CD); no `preview` env initially | Single-environment apps, per infra-brain precedent. |

## 3. Architecture

One repo → **one GHCR image** → deployed three times as **single-container Coolify apps**.
A single env var `BRAIN_TYPE` (`app` | `infra` | `open`) selects at startup: which toolset
to register, which DB to talk to, which migrations to run, and whether embeddings are
enabled. Every deployment runs the **same image**; the only per-brain differences are
**env vars + a separate managed Postgres database**.

```
GitHub Actions (on push to main)
  test → build & push ghcr.io/alobarquest/brain:{sha,latest} → fire 3 Coolify deploy webhooks
                                                                  │
        ┌─────────────────────────────────────────────────────────┼───────────────────────────────┐
        ▼                                                          ▼                                ▼
  Coolify app: brain-app          Coolify app: brain-infra              Coolify app: brain-open
  BRAIN_TYPE=app                  BRAIN_TYPE=infra                       BRAIN_TYPE=open
  FQDN app-brain.devonwatkins.com FQDN infra-brain.devonwatkins.com     FQDN open-brain.devonwatkins.com
        │                                │                                     │
        ▼                                ▼                                     ▼
  Coolify-managed Postgres        Coolify-managed Postgres              Coolify-managed Postgres
  (pgvector pg16, auto-backup)    (pgvector pg16, auto-backup)          (pgvector pg16, auto-backup)
```

Shared core (written once): FastAPI host, FastMCP mount at `/mcp` (+ the no-trailing-slash
alias shim), `x-brain-key` auth middleware, `/api/health`, async DB engine, OpenRouter
embeddings client, Dockerfile, `start.sh`.

## 4. Repo Structure

```
brain/
  Dockerfile                 # multi-stage, py3.12-slim, non-root appuser, EXPOSE 8000, CMD start.sh
  docker-compose.local.yml   # LOCAL DEV ONLY: api + pgvector db (prod has no compose)
  requirements.txt           # fastmcp>=3.4.2,<4, fastapi, uvicorn[standard], sqlalchemy, asyncpg, pgvector, httpx, pydantic*, alembic
  requirements-dev.txt
  scripts/start.sh           # per-brain alembic upgrade → optional per-brain seed → uvicorn :8000
  .bws-secrets.toml          # BWS secret UUID manifest consumed by this repo (no values)
  .github/workflows/ci.yml   # test → build/push GHCR → deploy (3 webhooks) → health poll
  src/
    core/
      app.py                 # FastAPI, mount FastMCP /mcp, load active brain via registry, /api/health
      config.py              # base Settings + BRAIN_TYPE enum + per-brain validation (port default 8000)
      db.py                  # async engine factory (pool 10/20/pre_ping/recycle 3600)
      auth.py                # x-brain-key / ?key= middleware, per-brain allowlist
      embeddings.py          # OpenRouter client; lazy, only when active brain.capabilities.embeddings
      mcp_alias.py           # MCPPrefixAlias shim (/mcp ≡ /mcp/)
      registry.py            # BRAIN_TYPE -> brain module
    brains/
      app/    {__init__.py (register + capabilities), tools.py, models.py, repositories.py, migrations/, alembic.ini}
      infra/  {…  embeddings=False  + seed.py …}
      open/   {…  embeddings=True   …}
  tests/
```

Production runs the **image only** (no compose). `docker-compose.local.yml` exists solely
for local dev (api + a pgvector Postgres) so contributors can run any `BRAIN_TYPE` locally.

## 5. How `BRAIN_TYPE` Wires Everything

(Unchanged from the original design.)

- **Tools:** `core/registry.py` maps `BRAIN_TYPE` → brain module; `app.py` calls
  `brain.register(mcp)`. Only the active brain's tools are registered.
- **Capabilities:** each brain declares flags (e.g. `embeddings`). OpenRouter initializes
  only when `brain.capabilities.embeddings` — Infra Brain never touches it.
- **Migrations:** `start.sh` runs `alembic -c src/brains/$BRAIN_TYPE/alembic.ini upgrade
  head` against the brain's own DB. **Existing migration files are relocated verbatim
  (revision IDs unchanged)** so after the data restore (see §9), the migrated DB's
  `alembic_version` matches and the first `upgrade head` is a clean no-op.
- **Seed:** after migrations, `start.sh` runs the brain's `seed.py --skip-existing` if
  present (only Infra Brain has one).
- **Auth allowlist (per-brain):** shared `auth.py` reads the active brain's allowlist.
  All brains (`infra`/`open`/`code`/`app`) allow only `/api/health`; every other path
  requires the access-key (`x-brain-key` header or `?key=`). **Superseded 2026-07-22:**
  `app` originally also allowlisted `/register` + `/.well-known/*` as unauthenticated
  OAuth-placeholder carryover. Those were removed — the brains are machine-to-machine and
  authenticate via shared secret, not OAuth, so no OAuth discovery/registration surface is
  needed and those paths must not bypass the gate. (Context: claude.ai's web connector now
  force-attempts OAuth DCR on any 401 MCP server, an Anthropic-side change unrelated to the
  brains; the fix is not to add OAuth but to keep the shared-secret model consistent.)
- **Config:** shared `Settings` (port=8000, `MCP_ACCESS_KEY`, DB creds, log level) +
  per-brain extras (`OPENROUTER_API_KEY` required for `app`/`open`). `MCP_ACCESS_KEY`
  validated as 64-char hex.

## 6. CI/CD (GitHub Actions)

On push to `main` (mirrors infra-brain's proven pipeline):
1. **test** — `pytest` (with a throwaway pgvector Postgres for DB-touching tests).
2. **build-and-push** — log in to GHCR via `GITHUB_TOKEN`; build and push
   `ghcr.io/alobarquest/brain:<full-sha>` and `:latest`; `type=gha` build cache.
3. **deploy** — **after** the push completes, fire the Coolify deploy webhook for **each
   of the three brain apps** (`?uuid=<app>&force=false`, bearer token); wait, then poll
   each `https://<brain>.devonwatkins.com/api/health` until 200 (fail the job if never).

Pin all third-party Actions to SHA/version tags. Required Actions secrets:
`COOLIFY_WEBHOOK_URL`, `COOLIFY_API_TOKEN`, and the three `COOLIFY_APP_UUID_{APP,INFRA,OPEN}`.

## 7. Secrets (BWS)

Per Devon's security standards: secrets live in **BWS**, referenced by **stable UUID**,
recorded in a committed **`.bws-secrets.toml`** manifest (UUIDs only — never values).
Coolify injects the values as environment variables at deploy time; the app reads plain
env vars at runtime (no BWS CLI in the entrypoint). Never commit a token or `.env`.

Secrets per brain: `MCP_ACCESS_KEY` (per brain), `POSTGRES_PASSWORD` (per managed DB),
`OPENROUTER_API_KEY` (app + open only). If any value is surfaced from a committed file
during migration, treat it as **leaked → rotate** (deletion is not enough).

## 8. Standardization Fixes (folded in by construction)

Shared core means the original drift disappears: one FastMCP pin (3.4.2), one
Dockerfile/`start.sh`, one auth implementation, pgvector pg16 everywhere, port 8000,
Coolify-FQDN domains, BWS secrets, single `/api/health` source of truth, and managed-DB
auto-backups.

## 9. Deployment & Cutover (Flavor B — includes one-time data migration)

Per brain (execute **one at a time**, verify before the next; **order: infra → open → app**):

1. **Snapshot** the current embedded DB: `pg_dump` the brain's existing compose Postgres
   to a file on the VPS (and/or a Hetzner server snapshot before starting). Record the old
   app's repo + deployed commit for rollback.
2. **Create** a new Coolify-managed Postgres resource (`pgvector/pgvector:pg16`, PG16),
   **enable Coolify auto-backup**. Use the brain's existing db/user names (`appbrain`,
   `infrabrain`, `openbrain`) so the restore's ownership matches.
3. **Restore** the `pg_dump` into the new managed DB (schema + data + `alembic_version`).
   For app/open, ensure `CREATE EXTENSION vector` is present (pgvector image provides it).
4. **Deploy** a new single-container Coolify app from `ghcr.io/alobarquest/brain:latest`
   (build pack `dockerimage`), env: `BRAIN_TYPE`, `POSTGRES_*` → the new managed DB,
   `MCP_ACCESS_KEY` (existing), `OPENROUTER_API_KEY` (app/open), port 8000; health check
   `/api/health` (127.0.0.1:8000, the settings in §2). Wire secrets from BWS.
5. **Verify:** `alembic upgrade head` is a **no-op** (proves the restore preserved revision
   state); `/api/health` → 200; an MCP tool call returns **existing data** (not empty).
6. **Swap domain:** move `<brain>.devonwatkins.com` (Coolify FQDN) to the new app; confirm
   HTTPS + an MCP call end-to-end.
7. **Decommission** the old compose app (stop; keep its volume until sign-off). **Rollback:**
   point the domain back at the old compose app (still intact) and restore from snapshot if
   data was touched.

After all three: archive `AlobarQuest/{app-brain,infra-brain,open-brain}` on GitHub;
each old README points to `AlobarQuest/brain`. Confirm Coolify auto-backups are running on
all three managed DBs.

## 10. Roadmap

- **Phase 1 (this spec):** build `brain` (Flavor B), CI/CD, then migrate + cut over all
  three. GHCR single artifact is in Phase 1 (no deferred build phase).
- **Later (optional):** `preview` environments; Bitbucket mirror; consolidating to a single
  multi-tenant service was explicitly rejected (fault isolation is permanent — §12).

## 11. Open Items / Risks

- **Data migration (new top risk):** embedded → managed Postgres per brain. Mitigated by
  one-at-a-time, `pg_dump` + Hetzner snapshot, alembic-no-op verification, domain-swap
  rollback. Keep old volumes until sign-off.
- **pgvector on a Coolify-managed DB:** confirm the managed Postgres resource can use the
  `pgvector/pgvector:pg16` image (or that `CREATE EXTENSION vector` is available) for
  app/open. Verify on the first embeddings brain (open) before app.
- **BWS wiring is new for these apps:** none of the brains use BWS today. Wiring the
  `.bws-secrets.toml` manifest + Coolify env injection is net-new work; validate one secret
  end-to-end before relying on it.
- **`stateless_http=True` + sampling:** all three set stateless HTTP; smoke-test any
  `ctx.sample()`/elicitation on 3.4.2 before cutover.
- **FastMCP v2→v3 kwarg sweep:** ensure transport kwargs are on `http_app(...)`, not the
  constructor (v3 hard-errors); grep ported code for removed v2 kwargs.
- **App Brain 502:** transient 502 seen during investigation while the container was
  healthy; watch post-cutover (may resolve under the unified 3.4.2 runtime).

## 12. Non-Goals

- Single multi-tenant service (runtime-B) — explicitly rejected; fault isolation is
  permanent.
- Changing any brain's tool semantics / APIs — this is a packaging/deploy unification.
- A `preview` environment in Phase 1.
