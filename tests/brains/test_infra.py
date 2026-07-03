"""Tests for the infra brain package: capabilities, tool registration, and registry guard."""
import importlib

import pytest
import sqlalchemy as sa
from fastmcp import FastMCP
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import src.brains.infra.tools.lessons as lessons_tools_module
import src.brains.infra.tools.rules as rules_tools_module
import src.core.db as db_module
import src.core.governance as governance_module
from src.brains.infra.models import Rule
from src.core.config import BrainType
from src.core.registry import Capabilities, load_brain

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_infra_capabilities():
    brain = load_brain(BrainType.INFRA)
    assert brain.capabilities.embeddings is False
    assert brain.capabilities.auth_exact == ("/api/health",)
    assert brain.capabilities.auth_prefixes == ()


def test_infra_capabilities_is_correct_type():
    brain = load_brain(BrainType.INFRA)
    assert isinstance(brain.capabilities, Capabilities)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    # rules
    "get_rules",
    "add_rule",
    "update_rule",
    "delete_rule",
    "restore_rule",
    # combos
    "get_combo",
    "list_combos",
    # lessons
    "search_lessons",
    "add_lesson",
    # versions
    "get_version",
    "list_versions",
    "update_version",
    "add_version",
    # governance
    "approve",
    "reject",
    "deprecate",
}


async def test_infra_registers_all_tools():
    brain = load_brain(BrainType.INFRA)
    mcp = FastMCP("t")
    brain.register(mcp)
    tools = await mcp.list_tools()
    registered = {t.name for t in tools}
    assert EXPECTED_TOOLS == registered


async def test_infra_register_is_idempotent_on_fresh_mcp():
    """Each call to register on a new mcp instance succeeds."""
    brain = load_brain(BrainType.INFRA)
    mcp1 = FastMCP("a")
    mcp2 = FastMCP("b")
    brain.register(mcp1)
    brain.register(mcp2)
    tools1 = {t.name for t in await mcp1.list_tools()}
    tools2 = {t.name for t in await mcp2.list_tools()}
    assert tools1 == EXPECTED_TOOLS
    assert tools2 == EXPECTED_TOOLS


# ---------------------------------------------------------------------------
# No embeddings client constructed (Step E spec)
# ---------------------------------------------------------------------------

def test_infra_no_embeddings():
    """The infra brain declares embeddings=False — no embeddings dependency needed."""
    brain = load_brain(BrainType.INFRA)
    assert brain.capabilities.embeddings is False


# ---------------------------------------------------------------------------
# Registry guard: transitive import errors must propagate (Step D)
# ---------------------------------------------------------------------------

def test_transitive_import_error_propagates(monkeypatch):
    """A ModuleNotFoundError whose .name is NOT the brain module must propagate, not be masked."""
    import src.core.registry as registry_module

    def _fake_import(name, *args, **kwargs):
        if name == "src.brains.infra":
            err = ModuleNotFoundError("No module named 'some_other_pkg'")
            err.name = "some_other_pkg"
            raise err
        return importlib.import_module(name, *args, **kwargs)

    monkeypatch.setattr(registry_module.importlib, "import_module", _fake_import)

    with pytest.raises(ModuleNotFoundError) as exc_info:
        load_brain(BrainType.INFRA)
    assert exc_info.value.name == "some_other_pkg"


def test_true_unknown_brain_raises_value_error():
    """A truly unknown brain (module doesn't exist) raises ValueError, not ModuleNotFoundError."""
    class FakeBrainType:
        value = "does_not_exist_xyz"

    with pytest.raises(ValueError, match="unknown brain"):
        load_brain(FakeBrainType())


# ---------------------------------------------------------------------------
# REST: GET /api/rules (restored 2026-06-20; consumed by the infraops audit)
# ---------------------------------------------------------------------------

import datetime

import httpx


class _FakeRule:
    id = 7
    severity = "BLOCK"
    category = "deployment"
    rule = "Never source-build on the VPS."
    reason = "CPU."
    source_app = "lifeops"
    check = None
    retired_at = None
    created_at = datetime.datetime(2026, 1, 2, 3, 4, 5)
    status = "approved"
    authority = "informational"
    applicability = {"category": "deployment", "source_app": "lifeops"}
    conflict_kind = None


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeRepo:
    def __init__(self, session):
        self.session = session

    async def list_all(self, category=None, severity=None, include_retired=False):
        return [_FakeRule()]


def _infra_app(monkeypatch):
    """Build the real infra app with the DB layer mocked out."""
    monkeypatch.setenv("BRAIN_TYPE", "infra")
    monkeypatch.setenv("MCP_ACCESS_KEY", "a" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("POSTGRES_HOST", "x")
    monkeypatch.setenv("POSTGRES_USER", "x")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("POSTGRES_DB", "x")

    import src.brains.infra as infra
    monkeypatch.setattr(infra, "get_session_factory", lambda: (lambda: _FakeSession()))
    monkeypatch.setattr(infra, "RuleRepository", _FakeRepo)

    from src.core.app import create_app
    return create_app()


async def test_api_rules_route_registered(monkeypatch):
    app = _infra_app(monkeypatch)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/rules" in paths


async def test_api_rules_requires_key(monkeypatch):
    app = _infra_app(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/rules")
    assert resp.status_code == 401


async def test_api_rules_returns_rules_with_key(monkeypatch):
    app = _infra_app(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/rules?key=" + "a" * 64)
    assert resp.status_code == 200
    body = resp.json()
    assert "rules" in body and len(body["rules"]) == 1
    r = body["rules"][0]
    assert r["id"] == 7 and r["severity"] == "BLOCK"
    # created_at is the REST-only field (the MCP get_rules tool omits it).
    assert r["created_at"] == "2026-01-02T03:04:05"


# ---------------------------------------------------------------------------
# Governance (WS-1.4): propose-only writes, safe-retrieval filters, conflicts
#
# Exercised against a real in-memory SQLite DB (not fakes) so find_conflicts'
# actual queries run — mirrors tests/core/test_governance.py's async-SQLite
# pattern.
# ---------------------------------------------------------------------------

def _sqlite_ddl_table(name: str, orm_table: sa.FromClause, metadata: sa.MetaData) -> sa.Table:
    """Clone an ORM table's columns onto a throwaway MetaData for the SQLite
    test DDL, substituting the Postgres-only JSONB type for portable JSON
    (SQLite's DDL compiler cannot render JSONB). ORM inserts/selects still
    compile against the real mapped table's column types (JSONB's Python-level
    (de)serialization works fine cross-dialect) — only CREATE TABLE needs the
    substitution."""
    cols = [
        sa.Column(
            c.name,
            sa.JSON() if isinstance(c.type, JSONB) else c.type,
            primary_key=c.primary_key,
            nullable=c.nullable,
            server_default=c.server_default,
        )
        for c in orm_table.columns
    ]
    return sa.Table(name, metadata, *cols)


def _data(result) -> dict:
    """Unwrap a mcp.call_tool() ToolResult's structured_content, which is
    typed Optional even though every infra tool returns a dict."""
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
async def infra_db(monkeypatch):
    """A real async-SQLite engine wired into the infra tool modules (and
    governance.py's lazily-imported get_session_factory), so governance write
    tools and find_conflicts run their actual queries end to end. Builds the
    physical schema from a JSONB->JSON-substituted clone of Rule's table
    rather than Base.metadata.create_all — other brains share the same
    declarative Base and register SQLite-incompatible types (e.g. open-brain's
    pgvector Thought)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    ddl_metadata = sa.MetaData()
    _sqlite_ddl_table(Rule.__tablename__, Rule.__table__, ddl_metadata)
    async with engine.begin() as conn:
        await conn.run_sync(ddl_metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(db_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(rules_tools_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(lessons_tools_module, "get_session_factory", lambda: factory)
    yield factory
    await engine.dispose()


def _infra_mcp() -> FastMCP:
    brain = load_brain(BrainType.INFRA)
    mcp = FastMCP("t")
    brain.register(mcp)
    return mcp


async def _seed_rule(factory, **overrides) -> int:
    """Insert a Rule row directly (bypassing add_rule), for seeding an
    already-approved conflict target."""
    defaults = dict(
        severity="BLOCK",
        category="security",
        rule="seed-rule",
        reason="seed",
        source_app=None,
        check=None,
        status="approved",
        authority="informational",
        applicability={},
        version=1,
    )
    defaults.update(overrides)
    async with factory() as session:
        rule = Rule(**defaults)
        session.add(rule)
        await session.commit()
        await session.refresh(rule)
        return rule.id


async def test_add_rule_is_proposed_by_default(infra_db):
    mcp = _infra_mcp()
    result = await mcp.call_tool(
        "add_rule",
        {"severity": "WARN", "category": "deployment", "rule": "r1", "reason": "why"},
    )
    body = _data(result)
    assert body["created"] is True
    assert body["status"] == "proposed"
    assert body["conflict"] is None
    async with infra_db() as session:
        rule = await session.get(Rule, body["id"])
        assert rule.status == "proposed"
        assert rule.authority == "informational"


async def test_get_rules_default_excludes_proposed_include_proposed_includes(infra_db):
    mcp = _infra_mcp()
    added = await mcp.call_tool(
        "add_rule",
        {"severity": "WARN", "category": "deployment", "rule": "r2", "reason": "why"},
    )
    new_id = _data(added)["id"]

    default = await mcp.call_tool("get_rules", {})
    assert new_id not in {r["id"] for r in _data(default)["rules"]}

    with_proposed = await mcp.call_tool("get_rules", {"include_proposed": True})
    ids = {r["id"] for r in _data(with_proposed)["rules"]}
    assert new_id in ids


async def test_get_rules_min_authority_filters(infra_db):
    required_id = await _seed_rule(
        infra_db, rule="required-rule", authority="required", category="security"
    )
    await _seed_rule(infra_db, rule="informational-rule", authority="informational")

    mcp = _infra_mcp()
    result = await mcp.call_tool("get_rules", {"min_authority": "required"})
    ids = {r["id"] for r in _data(result)["rules"]}
    assert ids == {required_id}


async def test_add_rule_duplicate_conflict_blocks_auto_approve_until_acknowledged(
    infra_db, monkeypatch
):
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    chk = {"kind": "forbidden_pattern", "scope": "tracked", "pattern": "P"}
    seed_id = await _seed_rule(
        infra_db,
        rule="bws.no-token-in-tracked-files",
        category="security",
        source_app=None,
        authority="required",
        check=chk,
    )

    mcp = _infra_mcp()
    added = await mcp.call_tool(
        "add_rule",
        {
            "severity": "BLOCK",
            "category": "security",
            "rule": "bws.no-token-in-tracked-files-dup",
            "reason": "candidate",
            "source_app": None,
            "check": chk,
            "proposed_by": "agent-x",
            "auto_approve": True,
        },
    )
    body = _data(added)
    assert body["conflict"] == "duplicate"
    assert body["status"] == "proposed"  # duplicate cancels auto-approve
    new_id = body["id"]

    async with infra_db() as session:
        new_rule = await session.get(Rule, new_id)
        assert new_rule.conflict_kind == "duplicate"
        assert new_rule.status == "proposed"
        seed_rule = await session.get(Rule, seed_id)
        assert seed_rule.status == "approved"
        assert seed_rule.conflict_kind is None

    no_ack = await mcp.call_tool("approve", {"record_type": "rule", "id": new_id})
    assert _data(no_ack)["error"] == "conflict_unacknowledged"

    with_ack = await mcp.call_tool(
        "approve", {"record_type": "rule", "id": new_id, "acknowledge_conflict": True}
    )
    assert _data(with_ack)["approved"] is True
    assert _data(with_ack)["status"] == "approved"

    async with infra_db() as session:
        approved_rule = await session.get(Rule, new_id)
        assert approved_rule.status == "approved"
        assert approved_rule.conflict_acknowledged_at is not None
        assert approved_rule.conflict_note is not None  # preserved, not cleared


async def test_add_rule_overlap_conflict_is_advisory_and_approves_without_ack(
    infra_db, monkeypatch
):
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    seed_id = await _seed_rule(
        infra_db,
        rule="bws.no-token-in-git-history",
        category="security",
        source_app="app1",
        authority="required",
        check={"kind": "forbidden_pattern", "pattern": "OLD"},
    )

    mcp = _infra_mcp()
    added = await mcp.call_tool(
        "add_rule",
        {
            "severity": "BLOCK",
            "category": "security",
            "rule": "bws.new-overlap-rule",
            "reason": "candidate",
            "source_app": "app1",
            "check": {"kind": "forbidden_pattern", "pattern": "NEW"},
            "proposed_by": "agent-x",
        },
    )
    body = _data(added)
    assert body["conflict"] == "overlap"
    new_id = body["id"]

    approved = await mcp.call_tool("approve", {"record_type": "rule", "id": new_id})
    assert _data(approved)["approved"] is True

    async with infra_db() as session:
        overlap_rule = await session.get(Rule, new_id)
        assert overlap_rule.conflict_kind == "overlap"
        assert overlap_rule.status == "approved"
        seed_rule = await session.get(Rule, seed_id)
        assert seed_rule.status == "approved"  # target row is never mutated
