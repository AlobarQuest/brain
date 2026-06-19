# Brain Unification Implementation Plan (Flavor B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one `brain` codebase → one **GHCR image** (Flavor B), deployed three times (`BRAIN_TYPE` = app|infra|open) as single-container Coolify apps each with its own **managed Postgres**, so the database is the only per-brain difference; then migrate + cut the three live brains over one at a time.

**Architecture:** Shared `src/core/` provides the FastAPI host, FastMCP `/mcp` mount, `x-brain-key` auth, async DB engine, OpenRouter embeddings, and `/api/health`. Each `src/brains/<type>/` contributes only its tools, models, repositories, Alembic tree, optional seed, and capability/allowlist declaration. `BRAIN_TYPE` selects the active brain at startup. GitHub Actions builds/pushes the image to GHCR and triggers Coolify deploys.

**Tech Stack:** Python 3.12, FastMCP 3.4.2, FastAPI, Uvicorn, SQLAlchemy 2.0 async + asyncpg, Alembic, pgvector, pydantic-settings, httpx, pytest, Docker, GitHub Actions, GHCR, Coolify (Flavor B), BWS.

**Design spec:** `docs/specs/2026-06-19-brain-unification-design.md` (read it first — especially §2 decisions, §6 CI/CD, §9 cutover).

**Source repos to port from (read-only references):**
- `~/Projects/infra-brain` — **canonical core reference** (already FastMCP 3); contributes the `infra` brain (rules/combos/lessons/versions, no embeddings, has a boot seed).
- `~/Projects/open-brain` — `open` brain (thought capture / semantic search, embeddings).
- `~/Projects/app-brain` — `app` brain (App/AppKnowledge, embeddings, `/register` + `/.well-known/*` unauthenticated allowlist).

## Global Constraints

- `fastmcp>=3.4.2,<4` (pin via floor 3.4.2). Construction: `FastMCP("brain")` then transport kwargs on `http_app(path="/", json_response=True, stateless_http=True)` — NOT on the constructor (v3 hard-errors).
- Python 3.12 (`python:3.12-slim`); container runs as non-root `appuser`; **`EXPOSE 8000`**; app listens on **`${PORT:-8000}`**.
- **Port 8000** everywhere (Dockerfile, uvicorn, config default, Coolify health-check port).
- DB image `pgvector/pgvector:pg16`; in production each brain gets its **own separate Coolify-managed Postgres resource** (auto-backup enabled). No embedded production Postgres.
- Secrets are **runtime-only** via env vars sourced from **BWS** (UUIDs in `.bws-secrets.toml`, never values). `MCP_ACCESS_KEY` must validate as `^[0-9a-f]{64}$`. Never commit a token or `.env`.
- `BRAIN_TYPE ∈ {app, infra, open}`; unknown/missing must fail fast at startup.
- **Health:** single endpoint `GET /api/health`, DB-aware → 200 `{status:"ok"}` / 503 `{status:"degraded"}`; exempt from auth. Coolify probe: host `127.0.0.1`, port `8000`, enabled, interval 10s, timeout 5s, retries 5, start_period 15s.
- **Alembic revision IDs preserved verbatim** when porting each brain's migrations (after the data restore, the migrated DB's `alembic_version` must match → first `upgrade head` is a no-op).
- **Domains:** Coolify FQDN field, `https://<brain>.devonwatkins.com`, Let's Encrypt.
- **Image:** `ghcr.io/alobarquest/brain:{<git-sha>,latest}`, built/pushed by GitHub Actions; Coolify build pack `dockerimage`.
- Default branch `main` (triggers CI). All work on a feature branch off `main`; frequent commits.

---

### Task 1: Repo scaffold, dependencies, test harness — ✅ DONE

Completed (commits `d0c3839`, `d4e46a0`): `requirements.txt`/`requirements-dev.txt`/`pyproject.toml`/package `__init__`s/`Makefile`/smoke test; `pydantic>=2.11` (fastmcp 3.x requires it). `make test` → 1 passing. No further action.

---

### Task 2: `core/config.py` — Settings + BRAIN_TYPE

**Files:** Create `src/core/config.py`, `tests/core/test_config.py`

**Interfaces — Produces:**
- `class BrainType(str, Enum)`: `APP="app"`, `INFRA="infra"`, `OPEN="open"`.
- `class Settings(BaseSettings)`: `brain_type: BrainType`, `mcp_access_key: str`, `log_level="INFO"`, `app_env="production"`, **`port: int = 8000`**, `postgres_host`, `postgres_port: int = 5432`, `postgres_user`, `postgres_password`, `postgres_db`, `openrouter_api_key: str | None = None`, `database_url: str | None = None`.
- `mcp_access_key` validated against `^[0-9a-f]{64}$`.
- `def effective_database_url(self) -> str` → `database_url` if set else `postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}`.
- `def get_settings() -> Settings` (cached).

- [ ] **Step 1: Failing tests** `tests/core/test_config.py`

```python
import pytest
from src.core.config import Settings, BrainType

BASE = dict(brain_type="infra", mcp_access_key="a"*64,
            postgres_host="db", postgres_user="u", postgres_password="p", postgres_db="d")

def test_brain_type_enum_and_url():
    s = Settings(**BASE)
    assert s.brain_type is BrainType.INFRA
    assert s.port == 8000
    assert s.effective_database_url() == "postgresql+asyncpg://u:p@db:5432/d"

def test_explicit_database_url_wins():
    s = Settings(**BASE, database_url="postgresql+asyncpg://x/y")
    assert s.effective_database_url() == "postgresql+asyncpg://x/y"

def test_bad_access_key_rejected():
    with pytest.raises(ValueError):
        Settings(**{**BASE, "mcp_access_key": "TOOSHORT"})

def test_unknown_brain_type_rejected():
    with pytest.raises(ValueError):
        Settings(**{**BASE, "brain_type": "bogus"})
```

- [ ] **Step 2: Run, verify fail** — `pytest tests/core/test_config.py -v`.
- [ ] **Step 3: Implement** `src/core/config.py`

```python
import re
from enum import Enum
from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

class BrainType(str, Enum):
    APP = "app"
    INFRA = "infra"
    OPEN = "open"

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    brain_type: BrainType
    mcp_access_key: str
    log_level: str = "INFO"
    app_env: str = "production"
    port: int = 8000
    postgres_host: str
    postgres_port: int = 5432
    postgres_user: str
    postgres_password: str
    postgres_db: str
    openrouter_api_key: str | None = None
    database_url: str | None = None

    @field_validator("mcp_access_key")
    @classmethod
    def _hex64(cls, v: str) -> str:
        if not _HEX64.match(v):
            raise ValueError("mcp_access_key must be 64 lowercase hex chars")
        return v

    def effective_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")

@lru_cache
def get_settings() -> "Settings":
    return Settings()
```

- [ ] **Step 4: Run, verify pass. Step 5: Commit** — `git commit -am "feat(core): config with BRAIN_TYPE, port 8000, DB url derivation"`

---

### Task 3: `core/db.py` — async engine factory

**Files:** Create `src/core/db.py`, `tests/core/test_db.py`

**Interfaces — Produces:** `make_engine(url) -> AsyncEngine` (pool_size 10, max_overflow 20, pool_pre_ping True, pool_recycle 3600); `make_sessionmaker(engine) -> async_sessionmaker`; `Base = declarative_base()`.

- [ ] **Step 1: Failing test** `tests/core/test_db.py`

```python
from src.core.db import make_engine, make_sessionmaker

def test_engine_pool_config():
    e = make_engine("postgresql+asyncpg://u:p@h:5432/d")
    assert e.pool.size() == 10
    assert make_sessionmaker(e) is not None
```

- [ ] **Step 2: Run, verify fail. Step 3: Implement**

```python
from sqlalchemy.ext.asyncio import (AsyncEngine, async_sessionmaker,
                                    create_async_engine)
from sqlalchemy.orm import declarative_base

Base = declarative_base()

def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_size=10, max_overflow=20,
                               pool_pre_ping=True, pool_recycle=3600)

def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 4: Pass. Step 5: Commit** — `git commit -am "feat(core): async engine factory"`

---

### Task 4: `core/registry.py` — brain protocol + lookup

**Files:** Create `src/core/registry.py`, `tests/core/test_registry.py`

**Interfaces — Produces:**
- `@dataclass(frozen=True) class Capabilities: embeddings: bool = False; auth_allowlist: tuple[str, ...] = ("/api/health",)`
- `class BrainModule(Protocol)`: attr `capabilities: Capabilities`; method `register(self, mcp) -> None`.
- `def load_brain(brain_type: BrainType) -> BrainModule` — imports `src.brains.<value>`; raises `ValueError` on unknown.

- [ ] **Step 1: Failing test** — scope to what exists now (brains land in Tasks 9–11): assert `Capabilities` defaults, and that `load_brain` raises `ValueError` for a value with no package. Use a monkeypatched bogus `BrainType` member or assert the import-error path via a name guaranteed absent.

```python
import pytest
from src.core.registry import Capabilities, load_brain

def test_capabilities_defaults():
    c = Capabilities()
    assert c.embeddings is False
    assert c.auth_allowlist == ("/api/health",)

def test_load_brain_unknown_raises():
    class Fake:  # mimic a BrainType with an unmapped value
        value = "does_not_exist"
    with pytest.raises(ValueError):
        load_brain(Fake())
```

(Real per-brain assertions are added in Tasks 9–11's tests, not here.)

- [ ] **Step 2: Run, verify fail. Step 3: Implement**

```python
import importlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from src.core.config import BrainType

@dataclass(frozen=True)
class Capabilities:
    embeddings: bool = False
    auth_allowlist: tuple[str, ...] = ("/api/health",)

@runtime_checkable
class BrainModule(Protocol):
    capabilities: Capabilities
    def register(self, mcp) -> None: ...

def load_brain(brain_type) -> BrainModule:
    try:
        return importlib.import_module(f"src.brains.{brain_type.value}")
    except ModuleNotFoundError as e:
        raise ValueError(f"unknown brain: {brain_type.value}") from e
```

- [ ] **Step 4: Pass. Step 5: Commit** — `git commit -am "feat(core): brain registry + Capabilities protocol"`

---

### Task 5: `core/auth.py` — x-brain-key middleware (per-brain allowlist)

**Files:** Create `src/core/auth.py`, `tests/core/test_auth.py`

**Interfaces — Produces:** `make_auth_middleware(access_key, allowlist)` returning a Starlette middleware. Allowlisted path-prefixes pass; others require header `x-brain-key` OR `?key=` equal (via `hmac.compare_digest`) to `access_key`, else 401 JSON. **Port from `~/Projects/infra-brain/src/main.py`'s `x-brain-key` middleware**, generalizing the hard-coded exempt set to the passed `allowlist` (prefix-match).

- [ ] **Step 1: Failing test** — tiny Starlette app + middleware (allowlist `("/api/health",)`, key `"a"*64`), via `httpx.ASGITransport`: `/api/health` → 200 no key; `/mcp` no key → 401; `/mcp?key=aaa…` → 200; wrong key → 401.
- [ ] **Step 2: Fail. Step 3: Port + generalize allowlist. Step 4: Pass. Step 5: Commit** — `git commit -am "feat(core): configurable x-brain-key auth middleware"`

---

### Task 6: `core/mcp_alias.py` — MCPPrefixAlias shim

**Files:** Create `src/core/mcp_alias.py`, `tests/core/test_mcp_alias.py`

**Interfaces — Produces:** `class MCPPrefixAlias` (ASGI middleware) making `/mcp` behave like `/mcp/`. **Port verbatim from `~/Projects/infra-brain/src/main.py`** (already FastMCP-3-correct).

- [ ] **Step 1: Failing test** — request scope path `/mcp` rewritten to `/mcp/` before the inner app (stub ASGI capturing `scope["path"]`).
- [ ] **Step 2: Fail. Step 3: Port. Step 4: Pass. Step 5: Commit** — `git commit -am "feat(core): port MCP prefix alias shim"`

---

### Task 7: `core/embeddings.py` — OpenRouter client (lazy)

**Files:** Create `src/core/embeddings.py`, `tests/core/test_embeddings.py`

**Interfaces — Produces:** `class EmbeddingsClient` with `async def embed(self, text) -> list[float]`; `get_embeddings_client(settings) -> EmbeddingsClient | None` returns `None` when no API key. **Port the OpenRouter call from `~/Projects/open-brain/src/services/openrouter.py`.**

- [ ] **Step 1: Failing test** — mock `httpx.AsyncClient.post` → fixed embedding; assert `embed()` returns it; assert factory returns `None` when key is `None`.
- [ ] **Step 2: Fail. Step 3: Port. Step 4: Pass. Step 5: Commit** — `git commit -am "feat(core): port OpenRouter embeddings client (lazy init)"`

---

### Task 8: `core/app.py` — FastAPI + FastMCP host + `/api/health`

**Files:** Create `src/core/app.py`, `tests/core/test_app_health.py`

**Interfaces — Produces:** `create_app() -> FastAPI`; module-level `app = create_app()` (uvicorn target `src.core.app:app`). Behavior:
1. `settings = get_settings()`; `brain = load_brain(settings.brain_type)`.
2. `mcp = FastMCP("brain")`; `brain.register(mcp)`.
3. `mcp_app = mcp.http_app(path="/", json_response=True, stateless_http=True)` mounted at `/mcp`; wrap with `MCPPrefixAlias`.
4. Auth middleware with `brain.capabilities.auth_allowlist`.
5. `GET /api/health` → `SELECT 1`; 200 `{status:"ok"}` / 503 `{status:"degraded"}`; in allowlist.
- **Port the FastAPI/FastMCP mounting from `~/Projects/infra-brain/src/main.py`** (FastMCP-3 reference), substituting registry-driven registration + per-brain allowlist. **Apply the v3 signature** (transport kwargs on `http_app`).

- [ ] **Step 1: Failing integration test** `tests/core/test_app_health.py` — env `brain_type=infra` + an aiosqlite `database_url`; `create_app()`; hit `/api/health` via `httpx.ASGITransport`; assert status in {200,503} and the JSON has a `status` field of the right shape.
- [ ] **Step 2: Fail. Step 3: Implement (port + v3 signature). Step 4: Pass. Step 5: Commit** — `git commit -am "feat(core): FastAPI+FastMCP host with registry wiring and /api/health"`

---

### Task 9: `brains/infra` — port Infra Brain (canonical, no embeddings)

**Files:** Create `src/brains/infra/{__init__.py,tools.py,models.py,repositories.py,seed.py,alembic.ini}`, `migrations/`, `tests/brains/test_infra.py`. Port from `~/Projects/infra-brain/src/{tools,repositories,db/models}.py`, `scripts/seed.py`, `seed/data.json`, `alembic/`.

**Interfaces — Produces:** `capabilities = Capabilities(embeddings=False, auth_allowlist=("/api/health",))`; `register(mcp)` registers rules/combos/lessons/versions tools. Models bind to `core.db.Base`.

- [ ] **Step 1:** Copy infra-brain's tool/model/repository modules into `src/brains/infra/`; rewire imports to `src.core.db.Base` + `src.core` services; expose `register(mcp)`.
- [ ] **Step 2:** Copy `alembic/` → `src/brains/infra/migrations/` **preserving every revision file + `revision`/`down_revision` IDs verbatim**; point `alembic.ini` `script_location` here; `env.py` reads `effective_database_url()`.
- [ ] **Step 3:** Port `seed.py` (+ `data.json`) with `--skip-existing`.
- [ ] **Step 4: Test** — `capabilities.embeddings is False`; build `FastMCP("t")`, `register(mcp)`, assert expected infra tool names registered; no embeddings client constructed.
- [ ] **Step 5: Pass. Step 6: Verify migration IDs** identical to source (`ls .../versions`). **Step 7: Commit** — `git commit -am "feat(brains): port infra brain"`

---

### Task 10: `brains/open` — port Open Brain (embeddings)

**Files:** Create `src/brains/open/{__init__.py,tools.py,models.py,repositories.py,alembic.ini}`, `migrations/`, `tests/brains/test_open.py`. Port from `~/Projects/open-brain/src/{tools/thoughts.py,repositories,db}`, `alembic/`.

**Interfaces — Produces:** `capabilities = Capabilities(embeddings=True, auth_allowlist=("/api/health",))`; `register(mcp)` registers thought tools, using `core.embeddings.get_embeddings_client`. Models use pgvector `Vector` on `core.db.Base`.

- [ ] **Step 1:** Port tools/models/repositories; route embeddings through `src.core.embeddings`. **Step 2:** Copy `alembic/` → `migrations/` preserving IDs. **Step 3: Test** — `embeddings is True`; thought tools registered; embedding client requested (mock). **Step 4: Pass. Step 5: Verify IDs. Step 6: Commit** — `git commit -am "feat(brains): port open brain"`

---

### Task 11: `brains/app` — port App Brain (embeddings + /register allowlist)

**Files:** Create `src/brains/app/{__init__.py,tools.py,models.py,repositories.py,alembic.ini}`, `migrations/`, `tests/brains/test_app.py`. Port from `~/Projects/app-brain/src/{tools,repositories,db/models}.py`, `alembic/`.

**Interfaces — Produces:** `capabilities = Capabilities(embeddings=True, auth_allowlist=("/api/health", "/register", "/.well-known"))`; `register(mcp)` registers App/AppKnowledge tools.

- [ ] **Step 1:** Port tools/models/repositories (App, AppKnowledge with `Vector(1536)`); embeddings via `core.embeddings`. **Step 2:** Copy `alembic/` → `migrations/` preserving IDs. **Step 3: Test** — `"/register" in capabilities.auth_allowlist`, `".well-known"` allowlisted, app tools registered. **Step 4: Pass — full suite `make test` green. Step 5: Verify IDs. Step 6: Commit** — `git commit -am "feat(brains): port app brain (preserve /register allowlist)"`

---

### Task 12: `scripts/start.sh` — per-brain migrate + seed + serve (:8000)

**Files:** Create `scripts/start.sh`, `tests/test_start_sh.py`

- [ ] **Step 1: Write `scripts/start.sh`**

```sh
#!/bin/sh
set -e
: "${BRAIN_TYPE:?BRAIN_TYPE is required}"
BRAIN_DIR="src/brains/${BRAIN_TYPE}"
[ -d "$BRAIN_DIR" ] || { echo "unknown BRAIN_TYPE: $BRAIN_TYPE" >&2; exit 1; }

alembic -c "${BRAIN_DIR}/alembic.ini" upgrade head

if [ -f "${BRAIN_DIR}/seed.py" ]; then
  python -m "src.brains.${BRAIN_TYPE}.seed" --skip-existing
fi

exec uvicorn src.core.app:app --host 0.0.0.0 --port "${PORT:-8000}"
```

- [ ] **Step 2: Test** `tests/test_start_sh.py` — `sh -n scripts/start.sh` exits 0; `BRAIN_TYPE` unset → non-zero; `BRAIN_TYPE=bogus` → non-zero (assert it fails before reaching alembic/uvicorn).
- [ ] **Step 3: Pass. Step 4: Commit** — `git commit -am "feat: per-brain start.sh (migrate, seed, serve :8000)"`

---

### Task 13: `Dockerfile` — multi-stage, non-root, the GHCR image

**Files:** Create `Dockerfile`, `.dockerignore`

**Interfaces — Produces:** the image pushed to `ghcr.io/alobarquest/brain`; `CMD sh /app/scripts/start.sh`; listens on 8000; runs as `appuser`.

- [ ] **Step 1: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY . .
RUN useradd -m appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
CMD ["sh", "/app/scripts/start.sh"]
```

- [ ] **Step 2: Write `.dockerignore`** (`.git`, `tests`, `__pycache__`, `.venv`, `docs`, `*.md`).
- [ ] **Step 3: Build** — `docker build -t brain:dev .` → succeeds.
- [ ] **Step 4: Commit** — `git commit -am "feat: multi-stage Dockerfile (non-root, port 8000)"`

---

### Task 14: `docker-compose.local.yml` + local 3-brain verification (DEV ONLY)

**Files:** Create `docker-compose.local.yml`, `.env.example`

**Interfaces — Produces:** a **local-dev-only** stack (`api` build `.` + `db` `pgvector/pgvector:pg16`) so any `BRAIN_TYPE` can run locally. **No production compose.** Maps host `8000→8000`. Env via `.env`.

- [ ] **Step 1: Write `docker-compose.local.yml`** — `api` (build `.`, `ports: 8000:8000`, `env_file: .env`, `depends_on: db: condition: service_healthy`) + `db` (`pgvector/pgvector:pg16`, `pg_isready` healthcheck, volume). Header comment: "LOCAL DEV ONLY — production deploys the GHCR image as a single-container Flavor-B app."
- [ ] **Step 2: Write `.env.example`** documenting every var (no secrets).
- [ ] **Step 3: Verify infra:** `.env` with `BRAIN_TYPE=infra`, throwaway `MCP_ACCESS_KEY` (64 hex), `POSTGRES_*=brain_local`. `docker compose -f docker-compose.local.yml up -d`; wait healthy; `curl -fsS localhost:8000/api/health` → `{status:ok}`; call one infra MCP tool over `/mcp` with the key. `docker compose -f docker-compose.local.yml down -v`.
- [ ] **Step 4: Repeat for `open` and `app`** (set `OPENROUTER_API_KEY`); confirm each registers only its own tools. **If any ported tool uses `ctx.sample()`/elicitation, smoke-test it here** (stateless_http risk, spec §11).
- [ ] **Step 5: Commit** — `git commit -am "feat: local-dev compose + 3-brain verification"`

---

### Task 15: GitHub Actions CI/CD — test → GHCR → deploy

**Files:** Create `.github/workflows/ci.yml`

**Interfaces — Produces:** on push to `main`: test, build/push `ghcr.io/alobarquest/brain:{sha,latest}`, then trigger the 3 Coolify deploy webhooks and health-poll. Model on `~/Projects/infra-brain/.github/workflows/` (read it for the exact webhook/poll pattern).

- [ ] **Step 1:** Write `.github/workflows/ci.yml` with 3 jobs:
  - **test:** checkout, setup-python 3.12, `pip install -r requirements-dev.txt`, `pytest -q`. (DB-touching tests use a `pgvector/pgvector:pg16` service container.)
  - **build-and-push** (needs test): GHCR login via `GITHUB_TOKEN`; `docker/build-push-action` → tags `:<sha>` + `:latest`; `cache-from/to: type=gha`. **Pin every third-party action to a SHA/tag.**
  - **deploy** (needs build-and-push): for each app UUID (`COOLIFY_APP_UUID_APP/INFRA/OPEN`), `curl` the Coolify webhook `"$COOLIFY_WEBHOOK_URL?uuid=<uuid>&force=false"` with `Authorization: Bearer $COOLIFY_API_TOKEN`; wait; poll `https://<brain>.devonwatkins.com/api/health` up to N times until 200 (fail otherwise). **Fire deploy webhooks only after the push completes** (job ordering enforces this).
- [ ] **Step 2: Lint** — `yamllint`/`actionlint` if available; otherwise validate structure by review. Document required repo Actions secrets in the workflow header comment: `COOLIFY_WEBHOOK_URL`, `COOLIFY_API_TOKEN`, `COOLIFY_APP_UUID_{APP,INFRA,OPEN}`.
- [ ] **Step 3: Commit** — `git commit -am "ci: GitHub Actions test → GHCR push → Coolify deploy"`

> Note: provisioning the GitHub repo secrets and the three Coolify apps happens in Task 17 (operational). This task only authors the workflow.

---

### Task 16: BWS secret manifest

**Files:** Create `.bws-secrets.toml`

**Interfaces — Produces:** a committed manifest of the **BWS secret UUIDs** this repo consumes (UUIDs only — never values), per `~/Projects/security-standards`.

- [ ] **Step 1:** Read `~/Projects/security-standards` for the `.bws-secrets.toml` format. Author `.bws-secrets.toml` listing the secrets the brains need, by stable UUID: `MCP_ACCESS_KEY` (per brain), `POSTGRES_PASSWORD` (per managed DB), `OPENROUTER_API_KEY` (app/open). Leave UUID placeholders to be filled when the BWS secrets are created in Task 17, OR fill them if the BWS entries already exist.
- [ ] **Step 2:** Confirm `.gitignore` covers `.env`/`.env.*` (it does). Run the security scanner if available (`security-standards` skill) → no BLOCK findings.
- [ ] **Step 3: Commit** — `git commit -am "chore: BWS secret manifest (.bws-secrets.toml)"`

> Actual BWS secret creation + Coolify env wiring is operational (Task 17).

---

### Task 17: Cutover runbook (Flavor B — managed DB + data migration, one brain at a time)

> Mutates live Coolify infra + migrates data. **Runbook, not TDD.** Execute **infra → open → app**, verifying each before the next. Devon has authorized infra mutation in this session, but this task runs only after explicit go-ahead at the Task 14 checkpoint.
>
> **Follow the project workflow:** app-brain (context) → infra-brain (pattern) → infraops (`coolify_*`) for every change. Use the `infraops` MCP tools only — never curl/SSH/UI.

**Files:** Create `docs/runbooks/2026-06-19-cutover.md` capturing the per-brain steps + verification + rollback.

**One-time prep:**
- [ ] Create BWS secrets for all brains; fill the UUIDs into `.bws-secrets.toml` (Task 16) and commit.
- [ ] Provision GitHub repo Actions secrets (`COOLIFY_WEBHOOK_URL`, `COOLIFY_API_TOKEN`, `COOLIFY_APP_UUID_{APP,INFRA,OPEN}`) — populated as each new Coolify app is created below.

**Per-brain procedure (template):**
- [ ] **Snapshot:** `vps_exec` `pg_dump` the brain's existing embedded Postgres → file on VPS; optionally `hetzner_create_snapshot` of the server. Record the old app's repo + deployed commit.
- [ ] **Create managed DB:** `coolify_create_database` → Postgres `pgvector/pgvector:pg16`, PG16, db/user matching the brain's existing names (`appbrain`/`infrabrain`/`openbrain`); **enable Coolify auto-backup**. For app/open verify the `vector` extension is available (open first — it's the canary).
- [ ] **Restore:** load the `pg_dump` into the new managed DB (schema + data + `alembic_version`). Confirm row counts match the source.
- [ ] **Create app:** `coolify_create_application_dockerimage` (or update an existing app) → image `ghcr.io/alobarquest/brain:latest`, build pack `dockerimage`; env (from BWS): `BRAIN_TYPE`, `POSTGRES_*` → new managed DB, `MCP_ACCESS_KEY` (existing value), `OPENROUTER_API_KEY` (app/open); port 8000; health check `/api/health` (127.0.0.1:8000, settings per Global Constraints). Add its UUID to the GitHub Actions secret.
- [ ] **Deploy** (`coolify_deploy`). Watch `coolify_application_logs`: confirm `alembic upgrade head` is a **no-op** (proves the restore preserved revision state).
- [ ] **Verify:** `/api/health` → 200; an MCP tool call returns **existing data** (not empty).
- [ ] **Swap domain:** set the brain's FQDN (`<brain>.devonwatkins.com`) on the new app (Coolify FQDN field); confirm HTTPS + an end-to-end MCP call.
- [ ] **Decommission** old compose app (stop; **keep its volume + the pg_dump until final sign-off**).
- [ ] **Rollback (if needed):** point the FQDN back at the old compose app (intact); if data was touched, restore from snapshot.

**Order & sign-off:**
- [ ] **infra** first (no embeddings; simplest; also validates the managed-DB + GHCR-deploy path). Stop, confirm.
- [ ] **open** second (first embeddings brain — validates pgvector on the managed DB; verify thought search returns existing thoughts).
- [ ] **app** third (verify app-knowledge queries + the `/register` unauthenticated path).
- [ ] **Post-cutover:** archive `AlobarQuest/{app-brain,infra-brain,open-brain}` (`gh repo archive`); old READMEs point to `AlobarQuest/brain`; confirm auto-backups running on all three managed DBs; commit the runbook.

---

## Self-Review

**Spec coverage:** Flavor B / single-container GHCR (Global + T13,T15,T17) · BRAIN_TYPE multi-instance (T2,T4,T8,T12) · new repo (T1) · FastMCP 3.4.2 (Global+T8) · port 8000 (Global+T2,T12,T13) · `/api/health` 127.0.0.1:8000 (T8,T17) · separate managed Postgres + auto-backup (T17) · pgvector pg16 (Global+T14,T17) · FQDN domains (T17) · BWS secrets (Global+T16,T17) · CI/CD GHCR→deploy→poll (T15) · data-migration cutover infra→open→app (T17) · Alembic ID preservation (T9–T11,T17). Spec §11 risks: pgvector-on-managed-DB (T17 open-canary), BWS-new-wiring (T16,T17), stateless+sampling (T14 Step 4), v2→v3 sweep (Global+T8), 502 watch (T17 verify).

**Placeholder scan:** code tasks (T2,T3,T4,T12,T13) carry complete code; port tasks (T5–T11) give exact source files + transformations + concrete test assertions (real impls live in the three repos — ported, not invented); ops tasks (T15–T17) reference the proven infra-brain pipeline + `infraops` tools. Intentional.

**Type consistency:** `BrainType`, `Settings.port`(=8000), `effective_database_url()`, `Capabilities(embeddings, auth_allowlist)`, `load_brain()`, `register(mcp)`, `get_embeddings_client()`, `create_app()` consistent across T2–T17.
