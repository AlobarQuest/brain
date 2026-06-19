# Brain Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one `brain` codebase/image, deployed three times (`BRAIN_TYPE` = app|infra|open), so the database is the only per-brain difference, then cut the three live brains over with zero data migration.

**Architecture:** Shared `src/core/` provides the FastAPI host, FastMCP `/mcp` mount, `x-brain-key` auth, async DB engine, OpenRouter embeddings, and health. Each `src/brains/<type>/` package contributes only its tools, models, repositories, Alembic tree, optional seed, and capability/allowlist declaration. `BRAIN_TYPE` selects the active brain at startup via `core/registry.py`.

**Tech Stack:** Python 3.12, FastMCP 3.4.2, FastAPI, Uvicorn, SQLAlchemy 2.0 async + asyncpg, Alembic, pgvector, pydantic-settings, httpx, pytest, Docker Compose, Coolify, Traefik.

**Source repos to port from (read-only references):**
- `~/Projects/infra-brain` — **canonical core reference** (already FastMCP 3); contributes the `infra` brain (rules/combos/lessons/versions, no embeddings, has a boot seed).
- `~/Projects/open-brain` — contributes the `open` brain (thought capture / semantic search, embeddings).
- `~/Projects/app-brain` — contributes the `app` brain (App/AppKnowledge, embeddings, `/register` + `/.well-known/*` unauthenticated allowlist).

**Design spec:** `docs/specs/2026-06-19-brain-unification-design.md` (read it first).

## Global Constraints

- `fastmcp>=3.4.2,<4` (pin 3.4.2). FastMCP construction: `FastMCP("brain")` then transport kwargs on `http_app(path="/", json_response=True, stateless_http=True)` — NOT on the constructor (v3 hard-errors).
- Python 3.12 (`python:3.12-slim`); container runs as non-root `appuser`; `EXPOSE 80`; app listens on `${PORT:-80}`.
- DB image `pgvector/pgvector:pg16` for every brain.
- Secrets are **runtime-only** (no build-time exposure). `MCP_ACCESS_KEY` must validate as `^[0-9a-f]{64}$`.
- `BRAIN_TYPE ∈ {app, infra, open}`; unknown/missing values must fail fast at startup.
- **Alembic revision IDs are preserved verbatim** when porting each brain's migrations (the live DBs' `alembic_version` rows must still match → first `upgrade head` is a no-op).
- Domain routing via Coolify `docker_compose_domains` only — no hand-wired Traefik labels in committed compose.
- Single health-check source of truth: the compose-level healthcheck. Coolify's app-level HTTP health check is disabled per app at cutover.
- All implementation work happens on a feature branch off `main`; frequent commits.

---

### Task 1: Repo scaffold, dependencies, test harness

**Files:**
- Create: `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `src/__init__.py`, `src/core/__init__.py`, `src/brains/__init__.py`, `tests/__init__.py`, `tests/conftest.py`, `Makefile`
- Exists: `.gitignore`

**Interfaces:**
- Produces: an installable dev environment; `pytest` runs green with one smoke test.

- [ ] **Step 1: Create `requirements.txt`** (pinned runtime deps)

```
fastmcp>=3.4.2,<4
fastapi==0.135.3
uvicorn[standard]==0.44.0
sqlalchemy==2.0.49
asyncpg==0.31.0
alembic==1.18.4
pgvector==0.3.6
httpx==0.28.1
pydantic==2.10.3
pydantic-settings==2.7.0
```

- [ ] **Step 2: Create `requirements-dev.txt`**

```
-r requirements.txt
pytest==8.3.4
pytest-asyncio==0.25.2
httpx==0.28.1
aiosqlite==0.20.0
```

- [ ] **Step 3: Create `pyproject.toml`** (pytest config only — deps stay in requirements.txt)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 4: Create package `__init__.py` files and a smoke test**

`tests/test_smoke.py`:
```python
def test_smoke():
    assert True
```

- [ ] **Step 5: Create `Makefile`**

```makefile
install: ; pip install -r requirements-dev.txt
test: ; pytest -q
```

- [ ] **Step 6: Install and run**

Run: `cd ~/Projects/brain && make install && make test`
Expected: 1 passed.

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "chore: scaffold brain repo deps and test harness"
```

---

### Task 2: `core/config.py` — Settings + BRAIN_TYPE

**Files:**
- Create: `src/core/config.py`, `tests/core/test_config.py`

**Interfaces:**
- Produces:
  - `class BrainType(str, Enum)` with `APP="app"`, `INFRA="infra"`, `OPEN="open"`.
  - `class Settings(BaseSettings)` fields: `brain_type: BrainType`, `mcp_access_key: str`, `log_level: str = "INFO"`, `app_env: str = "production"`, `port: int = 80`, DB fields `postgres_host`, `postgres_port: int = 5432`, `postgres_user`, `postgres_password`, `postgres_db`, optional `openrouter_api_key: str | None = None`, optional `database_url: str | None = None`.
  - `mcp_access_key` validated against `^[0-9a-f]{64}$`.
  - `def effective_database_url(self) -> str` → returns `database_url` if set else `postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}`.
  - `def get_settings() -> Settings` (cached).

- [ ] **Step 1: Write failing tests** `tests/core/test_config.py`

```python
import pytest
from src.core.config import Settings, BrainType

BASE = dict(brain_type="infra", mcp_access_key="a"*64,
            postgres_host="db", postgres_user="u", postgres_password="p", postgres_db="d")

def test_brain_type_enum_and_url():
    s = Settings(**BASE)
    assert s.brain_type is BrainType.INFRA
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

- [ ] **Step 2: Run, verify fail** — `pytest tests/core/test_config.py -v` → import error / fail.

- [ ] **Step 3: Implement `src/core/config.py`**

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
    port: int = 80
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

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git commit -am "feat(core): config with BRAIN_TYPE and DB url derivation"`

---

### Task 3: `core/db.py` — async engine factory

**Files:**
- Create: `src/core/db.py`, `tests/core/test_db.py`

**Interfaces:**
- Consumes: `Settings.effective_database_url()`.
- Produces: `def make_engine(url: str) -> AsyncEngine` (pool_size 10, max_overflow 20, pool_pre_ping True, pool_recycle 3600); `def make_sessionmaker(engine) -> async_sessionmaker`; `Base = declarative_base()` shared declarative base re-exported for brain models.

- [ ] **Step 1: Failing test** `tests/core/test_db.py`

```python
from src.core.db import make_engine, make_sessionmaker

def test_engine_pool_config():
    e = make_engine("postgresql+asyncpg://u:p@h:5432/d")
    assert e.pool.size() == 10
    assert make_sessionmaker(e) is not None
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** `src/core/db.py`

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

- [ ] **Step 4: Run, verify pass. Step 5: Commit** — `git commit -am "feat(core): async engine factory"`

---

### Task 4: `core/registry.py` — brain protocol + lookup

**Files:**
- Create: `src/core/registry.py`, `tests/core/test_registry.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Capabilities: embeddings: bool = False; auth_allowlist: tuple[str, ...] = ("/api/health",)`
  - `class BrainModule(Protocol)`: attribute `capabilities: Capabilities`; method `def register(self, mcp) -> None`; method `def models_module(self) -> str` (dotted path for Alembic target metadata).
  - `def load_brain(brain_type: BrainType) -> BrainModule` — imports `src.brains.<value>` and returns it; raises `ValueError` on unknown.

- [ ] **Step 1: Failing test** `tests/core/test_registry.py`

```python
import pytest
from src.core.config import BrainType
from src.core.registry import load_brain, Capabilities

def test_load_each_brain_has_capabilities():
    for bt in BrainType:
        brain = load_brain(bt)
        assert isinstance(brain.capabilities, Capabilities)
        assert callable(brain.register)

def test_infra_has_no_embeddings_and_minimal_allowlist():
    infra = load_brain(BrainType.INFRA)
    assert infra.capabilities.embeddings is False
    assert infra.capabilities.auth_allowlist == ("/api/health",)

def test_app_allowlist_includes_register():
    app = load_brain(BrainType.APP)
    assert "/register" in app.capabilities.auth_allowlist
```

- [ ] **Step 2: Run, verify fail** (brains not yet ported — this test goes green after Tasks 9–11; until then mark xfail or implement registry now and let brain-dependent asserts fail). Implement registry now; the per-brain asserts pass once Tasks 9–11 land.

- [ ] **Step 3: Implement** `src/core/registry.py`

```python
import importlib
from dataclasses import dataclass, field
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

def load_brain(brain_type: BrainType) -> BrainModule:
    try:
        return importlib.import_module(f"src.brains.{brain_type.value}")
    except ModuleNotFoundError as e:
        raise ValueError(f"unknown brain: {brain_type}") from e
```

- [ ] **Step 4: Run** — `test_load_each_brain` fails until brains exist; commit registry now, revisit assert in Task 11.
- [ ] **Step 5: Commit** — `git commit -am "feat(core): brain registry + Capabilities protocol"`

---

### Task 5: `core/auth.py` — x-brain-key middleware (per-brain allowlist)

**Files:**
- Create: `src/core/auth.py`, `tests/core/test_auth.py`

**Interfaces:**
- Consumes: `Capabilities.auth_allowlist`, `Settings.mcp_access_key`.
- Produces: `def make_auth_middleware(access_key: str, allowlist: tuple[str, ...])` returning a Starlette `BaseHTTPMiddleware` subclass instance/factory. Behavior: requests whose path starts with any allowlist entry pass through; others require header `x-brain-key` OR query `?key=` equal (via `hmac.compare_digest`) to `access_key`, else 401 JSON. Port the exact logic from `~/Projects/infra-brain/src/main.py` (its `x-brain-key` middleware) and generalize the allowlist from hard-coded to the passed tuple.

- [ ] **Step 1: Failing test** `tests/core/test_auth.py` — build a tiny Starlette app with the middleware (allowlist `("/api/health",)`, key `"a"*64`) and assert: `/api/health` → 200 without key; `/mcp` without key → 401; `/mcp?key=aaaa...` → 200; wrong key → 401. (Use `httpx.ASGITransport`.)
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** by porting infra-brain's middleware, replacing the hard-coded exempt set with the `allowlist` param and `hmac.compare_digest` comparison; prefix-match each allowlist entry.
- [ ] **Step 4: Run, verify pass. Step 5: Commit** — `git commit -am "feat(core): configurable x-brain-key auth middleware"`

---

### Task 6: `core/mcp_alias.py` — MCPPrefixAlias shim

**Files:**
- Create: `src/core/mcp_alias.py`, `tests/core/test_mcp_alias.py`

**Interfaces:**
- Produces: `class MCPPrefixAlias` (ASGI middleware) making `/mcp` behave like `/mcp/`. Port verbatim from `~/Projects/infra-brain/src/main.py` (its existing `MCPPrefixAlias`), which is already FastMCP-3-correct.

- [ ] **Step 1: Failing test** asserting a request scope path `/mcp` is rewritten to `/mcp/` before reaching the inner app (use a stub ASGI app capturing `scope["path"]`).
- [ ] **Step 2: Run, verify fail. Step 3: Port implementation. Step 4: Pass. Step 5: Commit** — `git commit -am "feat(core): port MCP prefix alias shim"`

---

### Task 7: `core/embeddings.py` — OpenRouter client (lazy)

**Files:**
- Create: `src/core/embeddings.py`, `tests/core/test_embeddings.py`

**Interfaces:**
- Consumes: `Settings.openrouter_api_key`.
- Produces: `class EmbeddingsClient` with `async def embed(self, text: str) -> list[float]`; `def get_embeddings_client(settings) -> EmbeddingsClient | None` returns `None` when the active brain has `embeddings=False` OR no API key. Port the OpenRouter call from `~/Projects/open-brain/src/services/openrouter.py`.

- [ ] **Step 1: Failing test** mocking `httpx.AsyncClient.post` to return a fixed embedding; assert `embed()` returns the vector; assert factory returns `None` when key is `None`.
- [ ] **Step 2: Fail. Step 3: Port from open-brain's `openrouter.py`. Step 4: Pass. Step 5: Commit** — `git commit -am "feat(core): port OpenRouter embeddings client (lazy init)"`

---

### Task 8: `core/app.py` — FastAPI + FastMCP host + health

**Files:**
- Create: `src/core/app.py`, `tests/core/test_app_health.py`

**Interfaces:**
- Consumes: `get_settings()`, `load_brain()`, `make_auth_middleware()`, `MCPPrefixAlias`, `make_engine()`.
- Produces: `def create_app() -> FastAPI`; module-level `app = create_app()` (uvicorn target `src.core.app:app`). Behavior:
  1. `settings = get_settings()`; `brain = load_brain(settings.brain_type)`.
  2. `mcp = FastMCP("brain")`; `brain.register(mcp)`.
  3. `mcp_app = mcp.http_app(path="/", json_response=True, stateless_http=True)` mounted at `/mcp`; wrap with `MCPPrefixAlias`.
  4. Add auth middleware with `brain.capabilities.auth_allowlist`.
  5. `GET /api/health` → runs `SELECT 1`; 200 `{status:"ok"}` or 503 `{status:"degraded"}`. Health is in the allowlist.
  - Port the FastAPI/FastMCP mounting structure from `~/Projects/infra-brain/src/main.py` (FastMCP-3 reference), substituting registry-driven tool registration and the per-brain allowlist.

- [ ] **Step 1: Failing integration test** `tests/core/test_app_health.py` — set env for `brain_type=infra` with a sqlite/aiosqlite `database_url`, build the app via `create_app()`, hit `/api/health` with `httpx.ASGITransport`, assert 200 and JSON `status`. (For DB-less CI, allow health to report `degraded`→503 when no DB; assert it's one of {200,503} and the body shape is correct.)
- [ ] **Step 2: Fail. Step 3: Implement** by porting infra-brain's `main.py` host wiring to the registry model. **Apply the FastMCP v3 signature** (transport kwargs on `http_app`, not the constructor). **Step 4: Pass. Step 5: Commit** — `git commit -am "feat(core): FastAPI+FastMCP host with registry wiring and health"`

---

### Task 9: `brains/infra` — port Infra Brain (canonical, no embeddings)

**Files:**
- Create: `src/brains/infra/__init__.py`, `tools.py`, `models.py`, `repositories.py`, `seed.py`, `alembic.ini`, `migrations/` (env.py + versions/*), `tests/brains/test_infra.py`
- Port from: `~/Projects/infra-brain/src/{tools,repositories,db/models}.py`, `scripts/seed.py`, `seed/data.json`, `alembic/`.

**Interfaces:**
- Produces: module-level `capabilities = Capabilities(embeddings=False, auth_allowlist=("/api/health",))` and `def register(mcp) -> None` registering rules/combos/lessons/versions tools. Models bind to `core.db.Base`.

- [ ] **Step 1:** Copy infra-brain's tool/model/repository modules into `src/brains/infra/`; rewire imports to `src.core.db.Base` and `src.core` services; expose `register(mcp)` that registers the same tools against the passed `mcp`.
- [ ] **Step 2:** Copy `alembic/` → `src/brains/infra/migrations/` **preserving every revision file and its `revision`/`down_revision` IDs verbatim**; point `alembic.ini` `script_location` at this dir and `sqlalchemy.url` to be supplied via env (`-x` or `env.py` reading `effective_database_url()`).
- [ ] **Step 3:** Port `seed.py` (+ `data.json`) as `src/brains/infra/seed.py` with the `--skip-existing` behavior.
- [ ] **Step 4: Test** `tests/brains/test_infra.py`: assert `capabilities.embeddings is False`; build a `FastMCP("t")`, call `register(mcp)`, and assert the expected infra tool names are registered (introspect `mcp`); assert no embeddings client is constructed.
- [ ] **Step 5:** Run `pytest tests/brains/test_infra.py -v` → pass.
- [ ] **Step 6: Verify migration IDs** — `ls src/brains/infra/migrations/versions` and diff IDs against `~/Projects/infra-brain/alembic/versions` (must be identical filenames/IDs).
- [ ] **Step 7: Commit** — `git commit -am "feat(brains): port infra brain (tools, models, migrations, seed)"`

---

### Task 10: `brains/open` — port Open Brain (embeddings)

**Files:**
- Create: `src/brains/open/{__init__.py,tools.py,models.py,repositories.py,alembic.ini}`, `migrations/`, `tests/brains/test_open.py`
- Port from: `~/Projects/open-brain/src/{tools/thoughts.py,repositories,db}`, `alembic/`.

**Interfaces:**
- Produces: `capabilities = Capabilities(embeddings=True, auth_allowlist=("/api/health",))`; `register(mcp)` registers thought-capture/semantic-search tools, using `core.embeddings.get_embeddings_client`. Models use `pgvector` `Vector` column on `core.db.Base`.

- [ ] **Step 1:** Port tools/models/repositories; route embedding calls through `src.core.embeddings`. **Step 2:** Copy `alembic/` → `migrations/` preserving revision IDs. **Step 3: Test** `tests/brains/test_open.py`: `capabilities.embeddings is True`; `register` adds the thought tools; embedding client is requested (mock). **Step 4:** Pass. **Step 5: Verify IDs vs source. Step 6: Commit** — `git commit -am "feat(brains): port open brain (thoughts + embeddings)"`

---

### Task 11: `brains/app` — port App Brain (embeddings + /register allowlist)

**Files:**
- Create: `src/brains/app/{__init__.py,tools.py,models.py,repositories.py,alembic.ini}`, `migrations/`, `tests/brains/test_app.py`
- Port from: `~/Projects/app-brain/src/{tools,repositories,db/models}.py`, `alembic/`.

**Interfaces:**
- Produces: `capabilities = Capabilities(embeddings=True, auth_allowlist=("/api/health", "/register", "/.well-known"))`; `register(mcp)` registers App/AppKnowledge tools.

- [ ] **Step 1:** Port tools/models/repositories (App, AppKnowledge with `Vector(1536)`); embeddings via `core.embeddings`. **Step 2:** Copy `alembic/` → `migrations/` preserving revision IDs. **Step 3: Test** `tests/brains/test_app.py`: `"/register" in capabilities.auth_allowlist`; `".well-known"`-prefixed path is allowlisted; app tools registered. **Step 4:** Pass — this also turns Task 4's registry asserts green. **Step 5: Verify IDs. Step 6:** Run full suite `make test` → all pass. **Step 7: Commit** — `git commit -am "feat(brains): port app brain (preserve /register unauthenticated allowlist)"`

---

### Task 12: `scripts/start.sh` — per-brain migrate + seed + serve

**Files:**
- Create: `scripts/start.sh`, `tests/test_start_sh.py`

**Interfaces:**
- Consumes: env `BRAIN_TYPE`, `POSTGRES_*`/`DATABASE_URL`, `PORT`.

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

exec uvicorn src.core.app:app --host 0.0.0.0 --port "${PORT:-80}"
```

- [ ] **Step 2: Test** `tests/test_start_sh.py`: run `sh -n scripts/start.sh` (syntax) and assert exit 0; run with `BRAIN_TYPE` unset → exit non-zero; with `BRAIN_TYPE=bogus` → exit non-zero. (Use `subprocess`, stub `alembic`/`uvicorn` on PATH or assert it fails before reaching them.)
- [ ] **Step 3: Run, verify pass. Step 4: Commit** — `git commit -am "feat: per-brain start.sh (migrate, optional seed, serve)"`

---

### Task 13: `Dockerfile` — multi-stage, non-root

**Files:**
- Create: `Dockerfile`, `.dockerignore`

**Interfaces:**
- Produces: an image whose `CMD` is `sh /app/scripts/start.sh`, listening on 80, running as `appuser`.

- [ ] **Step 1: Write `Dockerfile`** (port structure from infra-brain's, adjust paths)

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
EXPOSE 80
CMD ["sh", "/app/scripts/start.sh"]
```

- [ ] **Step 2: Write `.dockerignore`** (`.git`, `tests`, `__pycache__`, `.venv`, `docs`).
- [ ] **Step 3: Build** — `docker build -t brain:dev .` → succeeds.
- [ ] **Step 4: Commit** — `git commit -am "feat: multi-stage Dockerfile (non-root, port 80)"`

---

### Task 14: `docker-compose.yml` + local override — and local 3-brain bring-up

**Files:**
- Create: `docker-compose.yml`, `docker-compose.local.yml`, `.env.example`

**Interfaces:**
- Produces: `api` (build `.`) + `db` (`pgvector/pgvector:pg16`) services; api healthcheck hitting `/api/health`; `restart: unless-stopped`; volume `postgres_data`; **no hardcoded Traefik labels** (domain comes from Coolify `docker_compose_domains` at deploy). Env via `env_file: .env` with `${BRAIN_TYPE}`, `${POSTGRES_*}`, `${MCP_ACCESS_KEY}`, optional `${OPENROUTER_API_KEY}`.

- [ ] **Step 1: Write `docker-compose.yml`** with `api` + `db`, the api compose healthcheck:
```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:80/api/health')"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```
db healthcheck `pg_isready -U ${POSTGRES_USER}` (10s/5s/5). `api depends_on db: condition: service_healthy`. db image `pgvector/pgvector:pg16`, volume `postgres_data:/var/lib/postgresql/data`.
- [ ] **Step 2: Write `.env.example`** documenting every var (no real secrets).
- [ ] **Step 3: Local verification — infra:** create `.env` with `BRAIN_TYPE=infra`, a throwaway `MCP_ACCESS_KEY` (64 hex), `POSTGRES_*=brain_local`. `docker compose up -d`; wait healthy; `curl -fsS localhost:<mapped>/api/health` → `{status:ok}`; call one infra MCP tool over `/mcp` with the key → success. `docker compose down -v`.
- [ ] **Step 4: Repeat Step 3 for `BRAIN_TYPE=open` and `BRAIN_TYPE=app`** (each against a fresh throwaway DB; app/open also set `OPENROUTER_API_KEY`). Confirm each registers only its own tools.
- [ ] **Step 5: Commit** — `git commit -am "feat: compose (api+pgvector db) + local 3-brain verification"`

---

### Task 15: Cutover runbook (operational — execute one brain at a time)

> This task mutates live Coolify infra. It is a **runbook**, not TDD. Execute infra → open → app, verifying each before the next. Devon has explicitly authorized infra mutation in this build session.

**Files:**
- Create: `docs/runbooks/2026-06-19-cutover.md` (capture the steps + verification + rollback below).

**Per-brain procedure (template):**
- [ ] **Pre:** `coolify_create_database_backup` is N/A (embedded DB) — instead snapshot via `vps_exec` `pg_dump` of the brain's DB to a file on the VPS, OR `hetzner_create_snapshot` of the server before starting. Record the current deployed commit + repo for rollback.
- [ ] **Repoint source:** update the existing Coolify app (`coolify_update_application`) to git repo `AlobarQuest/brain`, branch `main`, keep build pack `dockercompose`.
- [ ] **Set env** (`coolify_bulk_set_app_env`, runtime-only): `BRAIN_TYPE`, existing `MCP_ACCESS_KEY`, `POSTGRES_USER/DB/PASSWORD` matching the brain's existing values (so existing data is owned/readable), `OPENROUTER_API_KEY` for app/open. Remove obsolete vars.
- [ ] **Set domain:** configure Coolify `docker_compose_domains` for the api service → the brain's existing FQDN; remove reliance on hand-wired Traefik labels.
- [ ] **Disable** Coolify app-level HTTP health check (compose healthcheck is authoritative).
- [ ] **Deploy** (`coolify_deploy`). Watch `coolify_application_logs`: confirm `alembic upgrade head` is a **no-op** (no new migrations applied) — proves revision-ID preservation and that the existing data is intact.
- [ ] **Verify:** `curl https://<brain>.devonwatkins.com/api/health` → 200 `{status:ok}`; call a representative MCP tool with the access key and confirm it returns existing data (not an empty DB).
- [ ] **Rollback (if needed):** repoint the app back to its original repo + restore env; redeploy. If data touched, restore from the pre-snapshot/pg_dump.

**Order & sign-off:**
- [ ] **infra** first (no embeddings; exercises the `postgres:16-alpine`→`pgvector/pgvector:pg16` image swap — confirm PGDATA mounts cleanly). Verify, then stop and confirm before proceeding.
- [ ] **open** second. Verify thought search returns existing thoughts (embeddings path works).
- [ ] **app** third. Verify app-knowledge queries + the `/register` unauthenticated path still behave.
- [ ] **Post-cutover:** archive `AlobarQuest/{app-brain,infra-brain,open-brain}` on GitHub (`gh repo archive`); record the unification in each old repo's README pointing to `AlobarQuest/brain`. Commit the runbook.

---

## Self-Review

**Spec coverage:** every spec §2 decision maps to a task — multi-instance/BRAIN_TYPE (T2,T4,T8,T12), new repo (T1), FastMCP 3.4.2 (Global + T1,T8), pgvector everywhere (T14,T15), Coolify domains (T14,T15), runtime-only secrets (T15), single health check (T8,T14,T15), repoint-existing cutover/zero-migration (T15), build-from-repo (T13). Spec §5 wiring → T4,T8,T12. §6 standardization → folded across core tasks. §7 cutover matrix → T15. §9 risks: FastMCP kwarg sweep (Global + T8), stateless+sampling smoke-test (add to T14 local verification), infra DB-image swap (T15 infra step), 502 watch (T15 verify).

**Placeholder scan:** core glue tasks (T2,T3,T4-impl note,T12,T13,T14) carry complete code; port tasks (T5–T11) specify exact source files + transformations + concrete test assertions rather than invented source — intentional, since the real implementations live in the three repos and must be ported, not rewritten.

**Type consistency:** `BrainType`, `Settings.effective_database_url()`, `Capabilities(embeddings, auth_allowlist)`, `load_brain()`, `register(mcp)`, `get_embeddings_client()`, `create_app()` names are used consistently across T2–T14.

**Added from review:** Task 14 Step 3/4 must include a `ctx.sample()`/elicitation smoke-test if any ported tool uses sampling (spec §9). Noted here.
