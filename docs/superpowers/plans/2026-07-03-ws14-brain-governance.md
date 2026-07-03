# WS-1.4 Brain Governance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the four brains (app/infra/open/code) a governed-knowledge record shape — lifecycle status, authority, provenance, applicability, supersession, version — plus a propose→approve lifecycle (approver-key-gated) and same-applicability conflict detection flagged at write.

**Architecture:** A shared, `Base`-agnostic `GovernanceMixin` + helper module in `src/core/governance.py` provides the columns, the approver gate (`require_approver`), the conflict engine (`normalized_check`/`find_conflicts`), and a governance-tool factory (`approve`/`reject`/`deprecate`). Each brain mixes the columns into its record models, adds one additive Alembic revision (ADD COLUMN + backfill), makes its write tools propose-only, adds safe-retrieval filters, and surfaces the new fields. Live schema migration is run by the orchestrator one brain at a time via the existing CI/CD path.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 (async, asyncpg), Alembic (per-brain chains), FastMCP 3.x, FastAPI/Starlette, pytest (asyncio auto), SQLite-in-memory for tests, Postgres 16 + pgvector in prod.

**Design spec:** `docs/superpowers/specs/2026-07-03-ws14-brain-governance-design.md` — read it before starting. Section refs below (§N) point at it.

## Global Constraints

- Python **3.12+**. Match existing module style (typed `Mapped[...]`/`mapped_column`, repository pattern, hand-built tool result dicts). Ruff line-length **100**, rules E/F/I/UP/B/C90; pyright basic (migrations excluded).
- **code-brain uses its OWN isolated `Base`** (`src/brains/code/models.py`) — never import `src.core.db.Base` there. The `GovernanceMixin` is a plain mixin (no `Base`), so it works with both.
- **`JSONB` is Postgres-only**; tests run on SQLite. Any new JSON column must be cross-dialect: `sa.JSON().with_variant(JSONB, "postgresql")`.
- **Additive migrations only** — `ADD COLUMN`; never rewrite/drop existing columns. One new Alembic revision per brain, `down_revision` = that brain's current head (infra `0003`, app `0003`, open `0001`, code `0001`).
- **Migrations run at container startup** (`scripts/start.sh` → `alembic -c <brain>/alembic.ini upgrade head`). Never at build time.
- **Approval is structural, not argument-trust.** The approver gate re-reads the caller's key; a contributor-key caller can never approve or `auto_approve`, regardless of arguments.
- **Governance vocabulary (use the constants, never string literals in call sites):** status ∈ {`proposed`,`approved`,`deprecated`,`superseded`}; authority ∈ {`informational`,`recommended`,`required`}; conflict_kind ∈ {`duplicate`,`overlap`,NULL}.
- Commit after every green task. Branch is `feat/ws14-brain-governance` (already checked out).
- `make check` does NOT exist as a Makefile target — run `pytest -q` (via `make test`) and `ruff check .` + `pyright` directly for verification.

## File Structure

- **`src/core/governance.py`** (NEW) — vocabulary constants, `GovernanceMixin`, `governance_check_constraints()`, `approver_ok`/`presented_key`/`require_approver`, `normalized_check`/`overlap_signature`/`find_conflicts`/`ConflictFlag`, `proposed_defaults()`, `register_governance_tools()`.
- **`src/core/config.py`** (MODIFY) — add optional `contributor_key`.
- **`src/core/auth.py`** (MODIFY) — accept approver OR contributor key.
- **`src/core/app.py`** (MODIFY) — pass `contributor_key` into the middleware.
- **Per brain** `src/brains/<b>/`: `models.py` (mixin + supersession), `migrations/versions/<next>_governance.py` (NEW), `tools/*.py` (propose-only + filters + surface fields + governance tools), `repositories/*.py` (status/authority-aware queries), `__init__.py` (register governance tools; REST field surfacing). code also `tools/serialize.py`.
- **Tests:** `tests/core/test_governance.py` (NEW), `tests/brains/test_<b>.py` (MODIFY).

---

### Task 1: Two-tier keys (config + auth middleware)

**Files:**
- Modify: `src/core/config.py`
- Modify: `src/core/auth.py`
- Modify: `src/core/app.py`
- Test: `tests/core/test_auth.py` (create if absent)

**Interfaces:**
- Produces: `Settings.contributor_key: str | None`; `make_auth_middleware(access_key, contributor_key=None, exact=..., prefixes=...)` accepting either key.

- [ ] **Step 1: Write the failing test**

Create/extend `tests/core/test_auth.py`:

```python
import hmac
import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from src.core.auth import make_auth_middleware

APPROVER = "a" * 64
CONTRIB = "b" * 64

def _client(contributor_key=None):
    async def ok(request):
        return PlainTextResponse("ok")
    app = Starlette(routes=[Route("/x", ok)])
    app.add_middleware(make_auth_middleware(APPROVER, contributor_key, ("/health",), ()))
    return TestClient(app)

def test_approver_key_accepted():
    assert _client().get("/x", headers={"x-brain-key": APPROVER}).status_code == 200

def test_no_key_rejected():
    assert _client().get("/x").status_code == 401

def test_contributor_key_accepted_when_configured():
    assert _client(CONTRIB).get("/x", headers={"x-brain-key": CONTRIB}).status_code == 200

def test_contributor_key_rejected_when_not_configured():
    assert _client(None).get("/x", headers={"x-brain-key": CONTRIB}).status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/Projects/brain && python -m pytest tests/core/test_auth.py -q`
Expected: FAIL (`make_auth_middleware` takes no `contributor_key`).

- [ ] **Step 3: Implement**

In `src/core/config.py`, add to `Settings` (mirror the `mcp_access_key` field/validator; a 64-lowercase-hex string, but Optional):

```python
    contributor_key: str | None = None
```
If `mcp_access_key` has a format validator, add an equivalent optional one for `contributor_key` (skip validation when None).

In `src/core/auth.py`, change the signature and the check:

```python
def make_auth_middleware(
    access_key: str,
    contributor_key: str | None = None,
    exact: tuple[str, ...] = ("/api/health",),
    prefixes: tuple[str, ...] = (),
) -> type[BaseHTTPMiddleware]:
    valid_keys = tuple(k for k in (access_key, contributor_key) if k)

    class BrainKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if path in exact or any(path.startswith(p) for p in prefixes):
                return await call_next(request)
            provided = request.headers.get("x-brain-key") or request.query_params.get("key")
            if not provided or not any(hmac.compare_digest(provided, k) for k in valid_keys):
                return JSONResponse(content={"error": "Invalid or missing access key"}, status_code=401)
            return await call_next(request)

    return BrainKeyMiddleware
```

In `src/core/app.py`, pass the contributor key:

```python
    app.add_middleware(
        make_auth_middleware(
            settings.mcp_access_key,
            settings.contributor_key,
            brain.capabilities.auth_exact,
            brain.capabilities.auth_prefixes,
        )
    )
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/core/test_auth.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/core/config.py src/core/auth.py src/core/app.py tests/core/test_auth.py
git commit -m "feat(core): two-tier brain keys (approver + optional contributor)"
```

---

### Task 2: Core governance — mixin, constants, approver gate

**Files:**
- Create: `src/core/governance.py`
- Test: `tests/core/test_governance.py`

**Interfaces:**
- Produces:
  - Constants: `STATUS_PROPOSED/APPROVED/DEPRECATED/SUPERSEDED`, `VALID_STATUSES`, `AUTHORITY_INFORMATIONAL/RECOMMENDED/REQUIRED`, `VALID_AUTHORITIES`, `AUTHORITATIVE=(recommended,required)`, `CONFLICT_DUPLICATE/OVERLAP`.
  - `GovernanceMixin` — declarative mixin with columns: `status, authority, proposed_by, owner, reviewed_by, reviewed_at, applicability, version, conflict_note, conflict_kind, conflict_acknowledged_at`.
  - `governance_check_constraints(tablename: str) -> tuple[CheckConstraint, ...]`.
  - `approver_ok(presented_key: str | None, approver_key: str) -> bool`
  - `presented_key() -> str | None` (reads FastMCP request headers)
  - `require_approver() -> bool`

- [ ] **Step 0 (SPIKE — do first): confirm FastMCP request-header access**

Run:
```bash
python -c "import fastmcp, inspect; print(fastmcp.__version__)"
python - <<'PY'
import importlib, pkgutil
try:
    m = importlib.import_module("fastmcp.server.dependencies")
    print("deps:", [n for n in dir(m) if "http" in n.lower() or "request" in n.lower()])
except Exception as e:
    print("no fastmcp.server.dependencies:", e)
PY
grep -rn "get_http_request\|get_http_headers\|get_context" $(python -c "import fastmcp,os;print(os.path.dirname(fastmcp.__file__))") | head
```
Record which callable exposes the inbound request/headers (expected `get_http_request` and/or `get_http_headers`). Use it in `presented_key()` below. If NEITHER exists in the installed version, STOP and report — the fallback is to stash the tier in the auth middleware (`scope["state"]`) and read it via `get_http_request().state`; adjust `presented_key()` accordingly. The rest of the plan is unchanged.

- [ ] **Step 1: Write the failing test**

`tests/core/test_governance.py`:

```python
import hmac
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from src.core import governance as g


class _Base(DeclarativeBase):
    pass


class _Rec(_Base, g.GovernanceMixin):
    __tablename__ = "recs"
    __table_args__ = g.governance_check_constraints("recs")
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(sa.Text)
    check: Mapped[dict | None] = mapped_column(sa.JSON, nullable=True)
    category: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    source_app: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


@pytest.fixture
def session():
    engine = sa.create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        yield s


def test_mixin_columns_exist_with_defaults(session):
    r = _Rec(name="x")
    session.add(r); session.commit(); session.refresh(r)
    assert r.status == "proposed"
    assert r.authority == "informational"
    assert r.version == 1
    assert r.applicability == {}
    assert r.conflict_kind is None


def test_status_check_constraint_rejects_bad_value(session):
    session.add(_Rec(name="y", status="bogus"))
    with pytest.raises(sa.exc.IntegrityError):
        session.commit()


def test_approver_ok():
    k = "a" * 64
    assert g.approver_ok(k, k) is True
    assert g.approver_ok("b" * 64, k) is False
    assert g.approver_ok(None, k) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/core/test_governance.py -q`
Expected: FAIL (`src.core.governance` missing).

- [ ] **Step 3: Implement `src/core/governance.py` (part 1)**

```python
"""Shared governance layer for the four brains: lifecycle columns, the
approver gate, the conflict engine, and the approve/reject/deprecate tools.

Base-agnostic on purpose — code-brain uses an isolated DeclarativeBase, so
GovernanceMixin must not reference any concrete Base.
"""
from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

# --- vocabulary -----------------------------------------------------------
STATUS_PROPOSED = "proposed"
STATUS_APPROVED = "approved"
STATUS_DEPRECATED = "deprecated"
STATUS_SUPERSEDED = "superseded"
VALID_STATUSES = (STATUS_PROPOSED, STATUS_APPROVED, STATUS_DEPRECATED, STATUS_SUPERSEDED)

AUTHORITY_INFORMATIONAL = "informational"
AUTHORITY_RECOMMENDED = "recommended"
AUTHORITY_REQUIRED = "required"
VALID_AUTHORITIES = (AUTHORITY_INFORMATIONAL, AUTHORITY_RECOMMENDED, AUTHORITY_REQUIRED)
AUTHORITATIVE = (AUTHORITY_RECOMMENDED, AUTHORITY_REQUIRED)
_AUTHORITY_RANK = {AUTHORITY_INFORMATIONAL: 0, AUTHORITY_RECOMMENDED: 1, AUTHORITY_REQUIRED: 2}

CONFLICT_DUPLICATE = "duplicate"
CONFLICT_OVERLAP = "overlap"

_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def governance_check_constraints(tablename: str) -> tuple[CheckConstraint, ...]:
    """DB-level CHECKs for a governed table. Compose into each model's
    __table_args__ (avoids the mixin/__table_args__ override collision)."""
    statuses = ", ".join(f"'{s}'" for s in VALID_STATUSES)
    authorities = ", ".join(f"'{a}'" for a in VALID_AUTHORITIES)
    return (
        CheckConstraint(f"status IN ({statuses})", name=f"ck_{tablename}_status"),
        CheckConstraint(f"authority IN ({authorities})", name=f"ck_{tablename}_authority"),
        CheckConstraint(
            "conflict_kind IS NULL OR conflict_kind IN ('duplicate', 'overlap')",
            name=f"ck_{tablename}_conflict_kind",
        ),
    )


class GovernanceMixin:
    """Governed-knowledge columns (companion §3.2). Base-agnostic."""

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("'proposed'"))
    authority: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sa.text("'informational'")
    )
    proposed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    applicability: Mapped[dict] = mapped_column(_JSON, nullable=False, server_default=sa.text("'{}'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa.text("1"))
    conflict_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_acknowledged_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


# --- approver gate --------------------------------------------------------
def approver_ok(presented: str | None, approver_key: str) -> bool:
    return bool(presented) and hmac.compare_digest(presented, approver_key)


def presented_key() -> str | None:
    """The x-brain-key the current caller presented, from the FastMCP HTTP
    request. Returns None outside an HTTP request (→ callers default deny)."""
    try:
        from fastmcp.server.dependencies import get_http_request  # confirmed in Step 0
        req = get_http_request()
    except Exception:
        return None
    if req is None:
        return None
    return req.headers.get("x-brain-key") or req.query_params.get("key")


def require_approver() -> bool:
    from src.core.config import get_settings
    return approver_ok(presented_key(), get_settings().mcp_access_key)
```

> Note: the CHECK-constraint SQL is rendered from the constant tuples so it never drifts from `VALID_STATUSES`/`VALID_AUTHORITIES`.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/core/test_governance.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/core/governance.py tests/core/test_governance.py
git commit -m "feat(core): GovernanceMixin + CHECK helper + approver gate"
```

---

### Task 3: Core governance — conflict engine

**Files:**
- Modify: `src/core/governance.py`
- Test: `tests/core/test_governance.py`

**Interfaces:**
- Consumes: constants + `GovernanceMixin` from Task 2; the `_Rec` test model.
- Produces:
  - `normalized_check(check: dict | None) -> str | None`
  - `overlap_signature(candidate: dict, key_fields: tuple[str, ...]) -> tuple | None` (None if any field is None)
  - `@dataclass ConflictFlag(kind: str, note: str)`
  - `async find_conflicts(session, model, *, candidate_check: dict | None, overlap_key_fields: tuple[str, ...], candidate: dict, exclude_id=None) -> ConflictFlag | None`

- [ ] **Step 1: Write the failing tests** (append to `tests/core/test_governance.py`)

```python
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker


def _norm(d):
    return g.normalized_check(d)


def test_normalized_check_is_key_order_independent():
    assert _norm({"a": 1, "b": 2}) == _norm({"b": 2, "a": 1})
    assert _norm(None) is None
    assert _norm({}) is None


def test_overlap_signature_skips_when_field_none():
    assert g.overlap_signature({"category": "security", "source_app": None}, ("category", "source_app")) is None
    assert g.overlap_signature({"category": "security", "source_app": "x"}, ("category", "source_app")) == ("security", "x")


@pytest.mark.asyncio
async def test_find_conflicts_duplicate_and_overlap():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    chk = {"kind": "forbidden_pattern", "scope": "tracked", "pattern": "P"}
    async with Session() as s:
        # an approved, required record is the only kind that is a conflict TARGET
        s.add(_Rec(name="base", status="approved", authority="required",
                   check=chk, category="security", source_app="app1"))
        await s.commit()
    async with Session() as s:
        dup = await g.find_conflicts(s, _Rec, candidate_check=dict(chk),
                                     overlap_key_fields=("category", "source_app"),
                                     candidate={"category": "security", "source_app": "app1"})
        assert dup is not None and dup.kind == g.CONFLICT_DUPLICATE
        over = await g.find_conflicts(s, _Rec, candidate_check={"kind": "x"},
                                      overlap_key_fields=("category", "source_app"),
                                      candidate={"category": "security", "source_app": "app1"})
        assert over is not None and over.kind == g.CONFLICT_OVERLAP
        none = await g.find_conflicts(s, _Rec, candidate_check={"kind": "y"},
                                      overlap_key_fields=("category", "source_app"),
                                      candidate={"category": "security", "source_app": "OTHER"})
        assert none is None


@pytest.mark.asyncio
async def test_informational_record_is_not_a_target():
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    chk = {"kind": "forbidden_pattern", "pattern": "P"}
    async with Session() as s:
        s.add(_Rec(name="info", status="approved", authority="informational", check=chk))
        await s.commit()
    async with Session() as s:
        assert await g.find_conflicts(s, _Rec, candidate_check=dict(chk),
                                      overlap_key_fields=(), candidate={}) is None
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/core/test_governance.py -q`
Expected: FAIL (`normalized_check`/`find_conflicts` missing).

- [ ] **Step 3: Implement (append to `src/core/governance.py`)**

```python
# --- conflict engine ------------------------------------------------------
@dataclass(frozen=True)
class ConflictFlag:
    kind: str  # CONFLICT_DUPLICATE | CONFLICT_OVERLAP
    note: str


def normalized_check(check: dict | None) -> str | None:
    """Canonical JSON of a machine check (sorted keys). None/empty → None."""
    if not check:
        return None
    return json.dumps(check, sort_keys=True, separators=(",", ":"))


def overlap_signature(candidate: dict, key_fields: tuple[str, ...]) -> tuple | None:
    """Applicability signature, or None if any key field is missing/None
    (a partial key is too coarse to flag)."""
    if not key_fields:
        return None
    vals = [candidate.get(f) for f in key_fields]
    if any(v is None for v in vals):
        return None
    return tuple(vals)


async def find_conflicts(
    session,
    model,
    *,
    candidate_check: dict | None,
    overlap_key_fields: tuple[str, ...],
    candidate: dict,
    exclude_id=None,
) -> ConflictFlag | None:
    """Flag a candidate against APPROVED, recommended|required records of the
    same model. Layer 1 (duplicate, byte-identical normalized check) wins over
    Layer 2 (applicability overlap). Only the candidate is flagged; no target
    row is mutated. Runs pre-commit so the candidate is not its own target."""
    stmt = sa.select(model).where(
        model.status == STATUS_APPROVED,
        model.authority.in_(AUTHORITATIVE),
    )
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    rows = (await session.execute(stmt)).scalars().all()

    cand_norm = normalized_check(candidate_check)
    cand_key = overlap_signature(candidate, overlap_key_fields)
    overlap_hit: ConflictFlag | None = None
    for r in rows:
        if cand_norm is not None and normalized_check(getattr(r, "check", None)) == cand_norm:
            return ConflictFlag(
                CONFLICT_DUPLICATE,
                f"duplicate check of approved {model.__tablename__} #{r.id}",
            )
        if cand_key is not None and overlap_hit is None:
            r_key = tuple(getattr(r, f, None) for f in overlap_key_fields)
            if r_key == cand_key:
                overlap_hit = ConflictFlag(
                    CONFLICT_OVERLAP,
                    f"overlaps approved {model.__tablename__} #{r.id} on {list(overlap_key_fields)}",
                )
    return overlap_hit
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/core/test_governance.py -q`
Expected: PASS (all governance tests).

- [ ] **Step 5: Commit**

```bash
git add src/core/governance.py tests/core/test_governance.py
git commit -m "feat(core): conflict engine (duplicate + overlap, proposal-only flag)"
```

---

### Task 4: Core governance — approve/reject/deprecate tool factory + proposed-defaults helper

**Files:**
- Modify: `src/core/governance.py`
- Test: `tests/core/test_governance.py`

**Interfaces:**
- Consumes: constants, `require_approver`, models.
- Produces:
  - `proposed_defaults(*, proposed_by: str | None, applicability: dict, auto_approve: bool) -> dict` — the governance fields to merge into an insert. Sets `status`/`authority`/`proposed_by`/`applicability`/`version`; if `auto_approve and require_approver()`, sets `status=approved` + `reviewed_by`/`reviewed_at`.
  - `finalize_governance(data: dict, flag: ConflictFlag | None) -> None` — writes `conflict_note`/`conflict_kind` into the insert dict AND, if the flag is a **duplicate**, downgrades an auto-approved insert back to `proposed` (never auto-approve over an unacknowledged duplicate; overlap stays advisory and does not downgrade).
  - `register_governance_tools(mcp, records: dict[str, type]) -> None` — registers `approve`/`reject`/`deprecate` MCP tools dispatching on `record_type` → model.
  - `APPROVER_IDENTITY = "devon"` (reviewed_by value; see note).

- [ ] **Step 1: Write the failing test** (append)

```python
def test_proposed_defaults_contributor_cannot_auto_approve(monkeypatch):
    monkeypatch.setattr(g, "require_approver", lambda: False)
    d = g.proposed_defaults(proposed_by="agent-x", applicability={"category": "security"}, auto_approve=True)
    assert d["status"] == "proposed"          # auto_approve ignored without approver key
    assert d["proposed_by"] == "agent-x"
    assert d["authority"] == "informational"


def test_proposed_defaults_approver_auto_approves(monkeypatch):
    monkeypatch.setattr(g, "require_approver", lambda: True)
    d = g.proposed_defaults(proposed_by="devon", applicability={}, auto_approve=True)
    assert d["status"] == "approved"
    assert d["reviewed_by"] == g.APPROVER_IDENTITY
    assert d["reviewed_at"] is not None


def test_finalize_governance_duplicate_cancels_auto_approve():
    d = {"status": "approved", "reviewed_by": "devon", "reviewed_at": "t"}
    g.finalize_governance(d, g.ConflictFlag(g.CONFLICT_DUPLICATE, "dup of #1"))
    assert d["status"] == "proposed"          # never auto-approve over a duplicate
    assert "reviewed_by" not in d and "reviewed_at" not in d
    assert d["conflict_kind"] == "duplicate"


def test_finalize_governance_overlap_is_advisory():
    d = {"status": "approved", "reviewed_by": "devon", "reviewed_at": "t"}
    g.finalize_governance(d, g.ConflictFlag(g.CONFLICT_OVERLAP, "overlaps #1"))
    assert d["status"] == "approved"          # overlap does not block auto-approve
    assert d["conflict_kind"] == "overlap"
```

- [ ] **Step 2: Run to verify fail**

Run: `python -m pytest tests/core/test_governance.py -k proposed_defaults -q`
Expected: FAIL.

- [ ] **Step 3: Implement (append to `src/core/governance.py`)**

```python
# --- write-path helpers + governance tools --------------------------------
APPROVER_IDENTITY = "devon"  # registry human-operator identity (WS-1.2); reviewed_by value


def proposed_defaults(*, proposed_by: str | None, applicability: dict, auto_approve: bool) -> dict:
    data: dict = {
        "status": STATUS_PROPOSED,
        "authority": AUTHORITY_INFORMATIONAL,
        "proposed_by": proposed_by or "unattributed",
        "applicability": applicability or {},
        "version": 1,
    }
    if auto_approve and require_approver():
        data["status"] = STATUS_APPROVED
        data["reviewed_by"] = APPROVER_IDENTITY
        data["reviewed_at"] = datetime.now(timezone.utc)
    return data


def finalize_governance(data: dict, flag: ConflictFlag | None) -> None:
    """Write the conflict flag onto an insert dict. A DUPLICATE flag also
    cancels any auto-approve (never approve over an unacknowledged duplicate);
    an OVERLAP flag is advisory and leaves auto-approve intact."""
    if flag is None:
        return
    data["conflict_note"] = flag.note
    data["conflict_kind"] = flag.kind
    if flag.kind == CONFLICT_DUPLICATE and data.get("status") == STATUS_APPROVED:
        data["status"] = STATUS_PROPOSED
        data.pop("reviewed_by", None)
        data.pop("reviewed_at", None)


def register_governance_tools(mcp, records: dict[str, type]) -> None:
    """Register approve/reject/deprecate for a brain's governed record types.
    `records` maps a record_type name → its GovernanceMixin model class."""
    from src.core.db import get_session_factory

    def _resolve(record_type: str):
        return records.get(record_type)

    @mcp.tool()
    async def approve(record_type: str, id: int, acknowledge_conflict: bool = False) -> dict:
        """Approve a proposed record (proposed→approved). APPROVER KEY ONLY.
        A duplicate-conflict flag must be acknowledged (acknowledge_conflict=True);
        an overlap flag is advisory and never blocks."""
        if not require_approver():
            return {"error": "not_authorized", "hint": "approval requires the approver key"}
        model = _resolve(record_type)
        if model is None:
            return {"error": "unknown_record_type", "allowed": list(records)}
        async with get_session_factory()() as session:
            rec = await session.get(model, id)
            if rec is None:
                return {"error": "not_found", "record_type": record_type, "id": id}
            if (rec.conflict_kind == CONFLICT_DUPLICATE
                    and rec.conflict_acknowledged_at is None and not acknowledge_conflict):
                return {"error": "conflict_unacknowledged", "conflict_note": rec.conflict_note}
            rec.status = STATUS_APPROVED
            rec.reviewed_by = APPROVER_IDENTITY
            rec.reviewed_at = datetime.now(timezone.utc)
            if rec.conflict_kind == CONFLICT_DUPLICATE and acknowledge_conflict:
                rec.conflict_acknowledged_at = datetime.now(timezone.utc)
            await session.commit()
            return {"approved": True, "record_type": record_type, "id": id, "status": rec.status}

    @mcp.tool()
    async def reject(record_type: str, id: int, reason: str) -> dict:
        """Reject a proposed record (→ deprecated). APPROVER KEY ONLY. The reason
        is appended to conflict_note (prefixed REJECTED:), preserving any flag."""
        if not require_approver():
            return {"error": "not_authorized"}
        model = _resolve(record_type)
        if model is None:
            return {"error": "unknown_record_type", "allowed": list(records)}
        async with get_session_factory()() as session:
            rec = await session.get(model, id)
            if rec is None:
                return {"error": "not_found", "record_type": record_type, "id": id}
            rec.status = STATUS_DEPRECATED
            rec.reviewed_by = APPROVER_IDENTITY
            rec.reviewed_at = datetime.now(timezone.utc)
            note = f"REJECTED: {reason}"
            rec.conflict_note = f"{rec.conflict_note} | {note}" if rec.conflict_note else note
            await session.commit()
            return {"rejected": True, "record_type": record_type, "id": id}

    @mcp.tool()
    async def deprecate(record_type: str, id: int) -> dict:
        """Deprecate an approved record (approved→deprecated). APPROVER KEY ONLY."""
        if not require_approver():
            return {"error": "not_authorized"}
        model = _resolve(record_type)
        if model is None:
            return {"error": "unknown_record_type", "allowed": list(records)}
        async with get_session_factory()() as session:
            rec = await session.get(model, id)
            if rec is None:
                return {"error": "not_found", "record_type": record_type, "id": id}
            rec.status = STATUS_DEPRECATED
            await session.commit()
            return {"deprecated": True, "record_type": record_type, "id": id}
```

> Note on `APPROVER_IDENTITY`: confirm the exact WS-1.2 registry identity for Devon-the-approver (security-standards `registry/`); if it differs from `"devon"`, use that string. Not load-bearing for tests.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/core/test_governance.py -q`
Expected: PASS.

- [ ] **Step 5: Verify full core lint/type + commit**

```bash
ruff check src/core/governance.py && pyright src/core/governance.py || true
git add src/core/governance.py tests/core/test_governance.py
git commit -m "feat(core): approve/reject/deprecate tools + proposed-defaults helper"
```

---

### Task 5: infra-brain — mixin, migration, propose-only tools, filters, governance tools (EXEMPLAR)

This is the fully-worked reference brain. Tasks 6–8 apply the same shape with brain-specific differences called out in full.

**Files:**
- Modify: `src/brains/infra/models.py`
- Create: `src/brains/infra/migrations/versions/0004_governance.py`
- Modify: `src/brains/infra/tools/rules.py`, `tools/lessons.py`, `tools/combos.py`
- Modify: `src/brains/infra/repositories/rules.py` (+ lessons/combos repos)
- Modify: `src/brains/infra/__init__.py`
- Test: `tests/brains/test_infra.py`

**Interfaces:**
- Consumes: everything from `src.core.governance` (Tasks 2–4).
- Produces: infra record models carry governance columns; `register()` also registers `approve`/`reject`/`deprecate` with `records={"rule": Rule, "lesson": Lesson, "combo": Combo}`.

- [ ] **Step 1: Add the mixin to the models**

In `src/brains/infra/models.py`: import `from src.core.governance import GovernanceMixin, governance_check_constraints`. For each of `Rule`, `Lesson`, `Combo` (NOT `Version`):
- add `GovernanceMixin` to the base list: `class Rule(Base, GovernanceMixin):`
- add `supersedes_id`/`superseded_by_id` self-FKs (int PK):
  ```python
      supersedes_id: Mapped[int | None] = mapped_column(sa.ForeignKey("rules.id"), nullable=True)
      superseded_by_id: Mapped[int | None] = mapped_column(sa.ForeignKey("rules.id"), nullable=True)
  ```
  (use the correct table name per model: `rules`, `lessons`, `combos`).
- compose the CHECK constraints into `__table_args__`. If the model already has `__table_args__` (e.g. `Rule` has the severity CHECK), append: `__table_args__ = (<existing...>, *governance_check_constraints("rules"))`. If it has none, add `__table_args__ = governance_check_constraints("lessons")`.

- [ ] **Step 2: Write the migration**

`src/brains/infra/migrations/versions/0004_governance.py` (down_revision = the current head — verify with `ls src/brains/infra/migrations/versions/`; expected `0003`):

```python
"""governance columns + backfill (WS-1.4)"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_TABLES = ("rules", "lessons", "combos")
_STATUSES = "'proposed', 'approved', 'deprecated', 'superseded'"
_AUTHORITIES = "'informational', 'recommended', 'required'"


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(t, sa.Column("status", sa.Text(), server_default="proposed", nullable=False))
        op.add_column(t, sa.Column("authority", sa.Text(), server_default="informational", nullable=False))
        op.add_column(t, sa.Column("proposed_by", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("owner", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("reviewed_by", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True))
        op.add_column(t, sa.Column("applicability", sa.dialects.postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False))
        op.add_column(t, sa.Column("version", sa.Integer(), server_default="1", nullable=False))
        op.add_column(t, sa.Column("conflict_note", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("conflict_kind", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("conflict_acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True))
        op.add_column(t, sa.Column("supersedes_id", sa.Integer(), nullable=True))
        op.add_column(t, sa.Column("superseded_by_id", sa.Integer(), nullable=True))
        op.create_check_constraint(f"ck_{t}_status", t, f"status IN ({_STATUSES})")
        op.create_check_constraint(f"ck_{t}_authority", t, f"authority IN ({_AUTHORITIES})")
        op.create_check_constraint(f"ck_{t}_conflict_kind", t, "conflict_kind IS NULL OR conflict_kind IN ('duplicate', 'overlap')")

    # --- backfill status: retired → deprecated, else approved (existing = pre-approved) ---
    op.execute("UPDATE rules SET status = 'deprecated' WHERE retired_at IS NOT NULL")
    op.execute("UPDATE rules SET status = 'approved' WHERE retired_at IS NULL")
    op.execute("UPDATE lessons SET status = 'approved'")
    op.execute("UPDATE combos SET status = 'approved'")
    # reviewed_* provenance for approved rows
    for t in _TABLES:
        op.execute(f"UPDATE {t} SET proposed_by = 'migration:ws-1.4'")
        op.execute(f"UPDATE {t} SET reviewed_by = 'migration:ws-1.4', reviewed_at = now() WHERE status = 'approved'")

    # --- backfill authority: enumerated scanner/engine-enforced security rules → required ---
    op.execute(
        """
        UPDATE rules SET authority = 'required'
        WHERE category = 'security'
          AND rule IN ('bws.no-token-in-tracked-files', 'bws.no-token-in-git-history',
                       'bws.bootstrap-token-not-inline', 'cred.exposure-rotate')
        """
    )

    # --- backfill applicability from existing fields (drives conflict keys + filtering) ---
    op.execute("UPDATE rules SET applicability = jsonb_build_object('category', category, 'source_app', source_app)")
    op.execute("UPDATE lessons SET applicability = jsonb_build_object('app', app)")
    op.execute("UPDATE combos SET applicability = jsonb_build_object('name', name, 'ecosystem', ecosystem)")


def downgrade() -> None:
    cols = ("status", "authority", "proposed_by", "owner", "reviewed_by", "reviewed_at",
            "applicability", "version", "conflict_note", "conflict_kind",
            "conflict_acknowledged_at", "supersedes_id", "superseded_by_id")
    for t in _TABLES:
        for c in ("status", "authority", "conflict_kind"):
            op.drop_constraint(f"ck_{t}_{c}", t, type_="check")
        for c in cols:
            op.drop_column(t, c)
```

- [ ] **Step 3: Make write tools propose-only + conflict-flagged**

In `src/brains/infra/tools/rules.py::add_rule`, add params `proposed_by: str | None = None, auto_approve: bool = False` and rewrite the insert to layer governance in. Example (rules):

```python
from src.core.governance import proposed_defaults, finalize_governance, find_conflicts

    @mcp.tool()
    async def add_rule(severity, category, rule, reason, source_app=None, check=None,
                       proposed_by=None, auto_approve=False):
        if severity not in ("BLOCK", "WARN", "INFO"):
            return {"error": "invalid_severity", "allowed": ["BLOCK", "WARN", "INFO"]}
        applicability = {"category": category, "source_app": source_app}
        data = {"severity": severity, "category": category, "rule": rule, "reason": reason,
                "source_app": source_app, "check": check}
        data.update(proposed_defaults(proposed_by=proposed_by, applicability=applicability,
                                      auto_approve=auto_approve))
        async with get_session_factory()() as session:
            flag = await find_conflicts(session, Rule, candidate_check=check,
                                        overlap_key_fields=("category", "source_app"),
                                        candidate={"category": category, "source_app": source_app})
            finalize_governance(data, flag)   # duplicate cancels auto-approve; overlap is advisory
            repo = RuleRepository(session)
            r = await repo.add(data)
            await session.commit()
            return {"created": True, "id": r.id, "status": r.status,
                    "conflict": flag.kind if flag else None}
```
Import `from src.brains.infra.models import Rule`. Apply the analogous change to `add_lesson` (`tools/lessons.py`, applicability `{"app": app}`) but **do NOT** call find_conflicts for lessons/combos (§7.2 excludes them) — only merge `proposed_defaults`. For `combos` there is no `add_combo` write tool in the current surface (combos are seed-only) — add only the migration columns + read-surface; no tool change needed.

**So: call `find_conflicts` only in `add_rule`.** For `add_lesson`, just merge `proposed_defaults` (applicability `{"app": app}`), no conflict call.

- [ ] **Step 4: Add safe-retrieval filters to read tools**

In `src/brains/infra/tools/rules.py::get_rules` add params `include_proposed: bool = False, min_authority: str | None = None`, and in `RuleRepository.list_all` filter: default `status == 'approved'` unless `include_proposed` (then also include `proposed`); always exclude `deprecated`/`superseded`; if `min_authority`, filter `authority` rank ≥ threshold. Add to `src/brains/infra/repositories/rules.py`:

```python
from src.core.governance import STATUS_APPROVED, STATUS_PROPOSED, _AUTHORITY_RANK

    async def list_all(self, category=None, severity=None, limit=100, include_retired=False,
                       include_proposed=False, min_authority=None):
        stmt = select(Rule)
        if category: stmt = stmt.where(Rule.category == category)
        if severity: stmt = stmt.where(Rule.severity == severity)
        if not include_retired:
            stmt = stmt.where(Rule.retired_at.is_(None))
        allowed = [STATUS_APPROVED] + ([STATUS_PROPOSED] if include_proposed else [])
        stmt = stmt.where(Rule.status.in_(allowed))
        if min_authority:
            ranks = [a for a, r in _AUTHORITY_RANK.items() if r >= _AUTHORITY_RANK[min_authority]]
            stmt = stmt.where(Rule.authority.in_(ranks))
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
```
(Rename the leading-underscore import if you prefer a public alias; add `AUTHORITY_RANK = _AUTHORITY_RANK` export in governance.py if pyright complains about the private import.)

Surface the new fields in `get_rules`' result dict: add `"status": r.status, "authority": r.authority, "applicability": r.applicability, "conflict": r.conflict_kind` to each rule dict.

- [ ] **Step 5: Reconcile soft-delete with status**

In `RuleRepository.retire`, also set `rule.status = STATUS_DEPRECATED`; in `restore`, set `rule.status = STATUS_APPROVED`. Import the constants.

- [ ] **Step 6: Register governance tools**

In `src/brains/infra/__init__.py::register(mcp)`, after the existing `register_*` calls:

```python
from src.core.governance import register_governance_tools
from src.brains.infra.models import Rule, Lesson, Combo
    register_governance_tools(mcp, {"rule": Rule, "lesson": Lesson, "combo": Combo})
```
If `__init__.py` exposes REST `GET /api/rules`, add the governance fields to that builder too.

- [ ] **Step 7: Write/extend tests** — `tests/brains/test_infra.py`

Add tests (use the file's existing fake/session pattern; if it monkeypatches the repo, add a real-SQLite test class for governance like in `tests/core/test_governance.py`):
- `add_rule` creates `status='proposed'`.
- `get_rules` default excludes a proposed rule; `include_proposed=True` includes it.
- `min_authority='required'` returns only required rules.
- **Conflict (exit criterion):** seed an approved `required` rule with slug `bws.no-token-in-tracked-files` and check `{"kind":"forbidden_pattern","scope":"tracked","pattern":"P"}`; `add_rule` with an identical check → response `conflict='duplicate'` and the new row has `conflict_kind='duplicate'`; the seeded row is unchanged; `approve('rule', new_id)` without ack → `conflict_unacknowledged`; with `acknowledge_conflict=True` → approved and `conflict_acknowledged_at` set, note preserved.
- **Overlap:** second security rule same `{category, source_app}` different check → `conflict='overlap'`; `approve` (no ack) succeeds.

- [ ] **Step 8: Run tests**

Run: `python -m pytest tests/brains/test_infra.py tests/core -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/brains/infra tests/brains/test_infra.py src/core/governance.py
git commit -m "feat(infra): governance columns, propose-only tools, conflict flag, filters"
```

---

### Task 6: code-brain — governance (isolated Base; roads/rules/lessons/exemplars; serialize.py)

Same shape as Task 5, with these code-brain specifics:

**Files:** `src/brains/code/models.py`, `migrations/versions/0002_governance.py` (down_revision=`0001`), `tools/{roads,rules,lessons,exemplars}.py`, `tools/serialize.py`, `repositories/*`, `__init__.py`, `tests/brains/test_code.py`.

- [ ] **Step 1:** Add `GovernanceMixin` to `Road`, `Rule`, `Lesson`, `Exemplar` (all four). Import the mixin/helper. `Road` and `Rule` ALREADY have `__table_args__` — append `*governance_check_constraints("roads")` / `("rules")`. `Lesson`/`Exemplar` have none — add `__table_args__ = governance_check_constraints("lessons")` / `("exemplars")`. Add int self-FKs `supersedes_id`/`superseded_by_id` per table.

- [ ] **Step 2:** Migration `0002_governance.py`, `_TABLES=("roads","rules","lessons","exemplars")`. Same column/CHECK adds. Backfill: `rules`/`roads` have `retired_at`? — **only `rules` has `retired_at`**; roads/lessons/exemplars have none, so `status='approved'` for all their rows; `rules`: retired→deprecated else approved. **authority:** code-brain has NO enumerated required rules at ship (§6.2) → leave all `informational` (no UPDATE-to-required). applicability: `rules` → `jsonb_build_object('road_slug', road_slug, 'category', category)`; `roads` → `jsonb_build_object('slug', slug)`; `lessons` → `jsonb_build_object('road_slug', road_slug)`; `exemplars` → `jsonb_build_object('road_slug', road_slug)`. Set `proposed_by='migration:ws-1.4'` and reviewed_* for approved rows.

- [ ] **Step 3:** Write tools propose-only: `add_road`, `add_rule`, `add_lesson`, `add_exemplar` get `proposed_by`/`auto_approve` + `proposed_defaults`. **Conflict detection only in `add_rule`** — but code rule checks may be `kind='judgment'` (opaque, not a real duplicate). Pass `candidate_check = check if (check and check.get("kind") != "judgment") else None`, `overlap_key_fields=("road_slug","category")`, `candidate={"road_slug": road_slug, "category": category}`. Others: `proposed_defaults` only.

- [ ] **Step 4:** Read filters (`include_proposed`, `min_authority`, default approved) in `get_rules`, `list_roads`, `get_road` (the composite — filter its rules/lessons/exemplars), `search`. **`tools/serialize.py`** — add `status`, `authority`, `applicability`, `conflict` (from `conflict_kind`) to `road_dict`, `rule_dict`, `lesson_dict`, `exemplar_dict`; the REST `/api/*` builders inherit this automatically.

- [ ] **Step 5:** Soft-delete: `retire_rule` also sets `status='deprecated'`.

- [ ] **Step 6:** `register_governance_tools(mcp, {"road": Road, "rule": Rule, "lesson": Lesson, "exemplar": Exemplar})` in `__init__.py`.

- [ ] **Step 7:** Tests in `tests/brains/test_code.py`: propose-only default; filters; and a duplicate-conflict test on code `rules` (seed an approved `required` code rule with a non-judgment `check`, propose an identical one → `duplicate`, ack-gated approve).

- [ ] **Step 8:** `python -m pytest tests/brains/test_code.py tests/core -q` → PASS.

- [ ] **Step 9:** Commit `feat(code): governance columns, propose-only tools, conflict flag, filters`.

---

### Task 7: app-brain — governance (app_knowledge; UUID PK; reuse supersedes_id; overlap-only)

**Files:** `src/brains/app/models.py`, `migrations/versions/0004_governance.py` (down_revision=`0003`), `tools/knowledge.py`, `repositories/*`, `__init__.py`, `tests/brains/test_app.py`.

- [ ] **Step 1:** Add `GovernanceMixin` to **`AppKnowledge` only** (NOT `App` — it's the app registry, excluded §3). `AppKnowledge` already has `supersedes_id` (UUID) + `is_active` — **reuse them**; add only `superseded_by_id` (UUID self-FK). `AppKnowledge` has no `__table_args__` in the model (indexes are in migrations) → add `__table_args__ = governance_check_constraints("app_knowledge")`.

- [ ] **Step 2:** Migration `0004_governance.py`, `_TABLES=("app_knowledge",)`. Add all mixin columns EXCEPT `supersedes_id` (already exists) — add `superseded_by_id UUID`. Backfill: `status = 'deprecated' WHERE is_active = false`, else `'approved'`. **authority:** all `informational` (no enumerated required in app-brain). applicability: `jsonb_build_object('app_slug', app_slug, 'knowledge_type', knowledge_type)`. `proposed_by`/reviewed_* as usual.

- [ ] **Step 3:** `capture_knowledge` propose-only: add `proposed_by`/`auto_approve`, merge `proposed_defaults` (applicability `{"app_slug":..., "knowledge_type":...}`). **Conflict:** app_knowledge has no `check` → overlap-only; call `find_conflicts(session, AppKnowledge, candidate_check=None, overlap_key_fields=("app_slug","knowledge_type"), candidate={...})` then `finalize_governance(data, flag)` (same as Task 5's `add_rule`). `onboard_app`'s bulk knowledge writes: keep them as-is but stamp `proposed_defaults(proposed_by="onboard", applicability=..., auto_approve=False)` per record (onboarded knowledge lands proposed unless Devon auto-approves — acceptable; note it).

- [ ] **Step 4:** Read filters in `search_knowledge` / `list_knowledge` (default approved; `include_proposed`; `min_authority`). Note `list_knowledge` already has `active_only` — keep it; add the status filter alongside (approved-and-active by default). Surface `status`/`authority`/`applicability`/`conflict` in result dicts.

- [ ] **Step 5:** `delete_knowledge` (soft-delete `is_active=false`) also sets `status='deprecated'`.

- [ ] **Step 6:** `register_governance_tools(mcp, {"app_knowledge": AppKnowledge})` in `__init__.py`.

- [ ] **Step 7:** Tests in `tests/brains/test_app.py`: propose-only default; approved-only retrieval default; overlap conflict when two `(app_slug, knowledge_type)` collide with an approved recommended/required record (seed one at `required` to make it a target).

- [ ] **Step 8:** `python -m pytest tests/brains/test_app.py tests/core -q` → PASS.

- [ ] **Step 9:** Commit `feat(app): governance columns, propose-only knowledge, overlap conflict, filters`.

---

### Task 8: open-brain — governance columns; thoughts approved/informational; NO approve tools; excluded from conflict

**Files:** `src/brains/open/models.py`, `migrations/versions/0002_governance.py` (down_revision=`0001`), `tools/thoughts.py`, `repositories/thoughts.py`, `tests/brains/test_open.py`.

- [ ] **Step 1:** Add `GovernanceMixin` to `Thought`. UUID PK → add `supersedes_id`/`superseded_by_id` as UUID self-FKs. Add `__table_args__ = governance_check_constraints("thoughts")`.

- [ ] **Step 2:** Migration `0002_governance.py`, `_TABLES=("thoughts",)`. Add all mixin columns + UUID supersession. Backfill: **`status='approved'`, `authority='informational'`** for ALL existing thoughts (Sub-B: observations, no approval gate). applicability: `'{}'::jsonb` (thoughts excluded from conflict; no meaningful key). `proposed_by='migration:ws-1.4'`, reviewed_* set (approved).

- [ ] **Step 3:** `capture_thought` (Sub-B): create with `status='approved'`, `authority='informational'`, `proposed_by='mcp'` — do **NOT** use `proposed_defaults` (that defaults to proposed). Set these explicitly in the `ThoughtRepository.create` insert (add the governance fields to the values). **No conflict detection.** **Do NOT** call `register_governance_tools` for open-brain (no in-place approve/reject; promotion is WS-6.2) — add a one-line comment saying so.

- [ ] **Step 4:** Read filters: open-brain reads (`search_thoughts`, `list_thoughts`) — since all thoughts are `approved`, the approved-default returns them unchanged; add `min_authority` is moot (all informational) — you MAY skip adding filter params here to avoid churn, but DO surface `status`/`authority` in nothing user-facing is required (thoughts render as prose). Minimal change: none needed to reads beyond what the migration does. (Document this choice in the test.)

- [ ] **Step 5:** Tests in `tests/brains/test_open.py`: `capture_thought` creates `status='approved'`, `authority='informational'`; no `approve` tool is registered for open-brain (assert it's absent from the MCP tool set, or simply that `register` doesn't call the factory).

- [ ] **Step 6:** `python -m pytest tests/brains/test_open.py tests/core -q` → PASS.

- [ ] **Step 7:** Commit `feat(open): governance columns; thoughts land approved/informational`.

---

### Task 9: Cross-cutting verification — full suite, lint, type, migration dry-run harness

**Files:** none new (verification + a throwaway harness script under scratchpad).

- [ ] **Step 1: Full test suite**

Run: `cd ~/Projects/brain && python -m pytest -q`
Expected: all green (record the collected count — the `uv sync` invariant means a zero-collected run is a failure signal).

- [ ] **Step 2: Lint + type**

Run: `ruff check . && pyright`
Expected: no NEW violations (baseline tracked by the code-standards Stop hook). Fix any introduced.

- [ ] **Step 3: Migration smoke against throwaway Postgres (per brain)**

For each brain, run its new revision against an empty `pgvector/pgvector:pg16` container to confirm `upgrade`+`downgrade` are clean (data backfill is validated separately by the orchestrator against restored dumps — §10):
```bash
docker run -d --rm --name gov-mig -e POSTGRES_PASSWORD=x -p 55432:5432 pgvector/pgvector:pg16
# wait for readiness (double-probe), then per brain:
DATABASE_URL=postgresql+asyncpg://postgres:x@127.0.0.1:55432/postgres \
  alembic -c src/brains/infra/alembic.ini upgrade head
DATABASE_URL=... alembic -c src/brains/infra/alembic.ini downgrade -1
# repeat for code/app/open (fresh DB each, or distinct DB names)
docker stop gov-mig
```
Expected: upgrade + downgrade succeed for all four with no error. (This is a schema smoke; the data-backfill assertions run in the orchestrator's restored-dump harness.)

- [ ] **Step 4: Commit any fixes**

```bash
git add -A && git commit -m "test(ws14): full-suite + migration smoke green" || echo "nothing to commit"
```

---

## Self-review notes (author)

- **Spec coverage:** §3 tables → Tasks 5–8 (versions/apps excluded). §4 columns → Task 2 mixin + per-brain migrations. §5 lifecycle/keys → Tasks 1,4,5–7. §6 backfill → each migration (Task 5/6/7/8). §7 conflict → Tasks 3,5 (+6,7 tests). §8 retrieval → per-brain read-filter steps. §9 tests → each task's test step + Task 9. §10 execution → orchestrator section below. All covered.
- **Open-brain asymmetry is intentional** (no approve tools, approved/informational, excluded from conflict) — per Sub-B.
- **`AUTHORITY_RANK` export:** if the private `_AUTHORITY_RANK` import reads poorly, add `AUTHORITY_RANK = _AUTHORITY_RANK` in governance.py and import that.

## Orchestrator-run migration (NOT a subagent task — see spec §10)

After the branch is review-clean and **Devon merges**:
1. Pre-merge (before the merge signal): restore each brain's latest `vps-production` dump into a throwaway `pgvector:pg16`, run its revision `upgrade head`, assert backfill counts (infra `required`==the 4 BWS/cred rules present; `deprecated`==retired count; thoughts all approved), MCP smoke, `downgrade`. Orchestrator runs this directly.
2. Post-merge, one brain at a time (order infra → code → app → open): optional `~/Projects/vps-backup/backup.sh` (zero-RPO) → deploy via existing CI/CD (infraops, never SSH/UI) → verify `/api/health`, MCP `propose → approve → retrieve honors authority`, `min_authority` filter → next brain.
3. Rollback: additive columns are backward-compatible; redeploy prior image if needed, `downgrade` as last resort.
