"""Tests for the infra brain package: capabilities, tool registration, and registry guard."""

import importlib

import pytest
import sqlalchemy as sa
from fastmcp import FastMCP
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import src.brains.infra.seed as infra_seed_module
import src.brains.infra.tools.combos as combos_tools_module
import src.brains.infra.tools.lessons as lessons_tools_module
import src.brains.infra.tools.rules as rules_tools_module
import src.core.db as db_module
import src.core.governance as governance_module
from src.brains.infra.models import Combo, Lesson, Rule
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


class _FakeLesson:
    id = 9
    app = "orchestrator"
    title = "Use bounded observations"
    content = "Promote only bounded facts through review."
    tags = ["ws62"]
    severity = "INFO"
    source = None
    status = "proposed"
    authority = "recommended"
    applicability = {"app": "orchestrator"}
    conflict_kind = None


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        return None


class _FakeRepo:
    def __init__(self, session):
        self.session = session

    async def list_all(self, category=None, severity=None, include_retired=False):
        return [_FakeRule()]


def _infra_app(monkeypatch):
    """Build the real infra app with the DB layer mocked out."""
    monkeypatch.setenv("BRAIN_TYPE", "infra")
    monkeypatch.setenv("MCP_ACCESS_KEY", "a" * 64)
    monkeypatch.setenv("CONTRIBUTOR_KEY", "b" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("POSTGRES_HOST", "x")
    monkeypatch.setenv("POSTGRES_USER", "x")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("POSTGRES_DB", "x")

    import src.brains.infra as infra

    monkeypatch.setattr(infra, "get_session_factory", lambda: lambda: _FakeSession())
    monkeypatch.setattr(infra, "RuleRepository", _FakeRepo)

    from src.core.app import create_app

    return create_app()


async def test_api_rules_route_registered(monkeypatch):
    app = _infra_app(monkeypatch)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/rules" in paths
    assert "/api/proposals/lessons" in paths
    assert "/api/proposals/rules" in paths


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


async def test_api_proposes_lesson_with_contributor_key(monkeypatch):
    app = _infra_app(monkeypatch)

    import src.brains.infra as infra

    async def fake_propose_lesson(session, **kwargs):
        assert kwargs["authority"] == "recommended"
        return _FakeLesson(), None

    monkeypatch.setattr(infra, "propose_lesson", fake_propose_lesson)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.post(
            "/api/proposals/lessons",
            headers={"x-brain-key": "b" * 64},
            json={
                "title": "Use bounded observations",
                "content": "Promote only bounded facts through review.",
                "app": "orchestrator",
                "tags": ["ws62"],
                "severity": "INFO",
                "proposed_by": "devon",
                "authority": "recommended",
            },
        )

    assert resp.status_code == 201
    body = resp.json()
    assert body["lesson"]["status"] == "proposed"
    assert body["lesson"]["authority"] == "recommended"


# ---------------------------------------------------------------------------
# Governance (WS-1.4): propose-only writes, safe-retrieval filters, conflicts
#
# Exercised against a real in-memory SQLite DB (not fakes) so find_conflicts'
# actual queries run — mirrors tests/core/test_governance.py's async-SQLite
# pattern.
# ---------------------------------------------------------------------------


def _sqlite_ddl_table(name: str, orm_table: sa.FromClause, metadata: sa.MetaData) -> sa.Table:
    """Clone an ORM table's columns onto a throwaway MetaData for the SQLite
    test DDL, substituting Postgres-only types (JSONB, ARRAY) for portable JSON
    (SQLite's DDL compiler cannot render either). ORM inserts/selects still
    compile against the real mapped table's column types (JSONB's and ARRAY's
    Python-level (de)serialization works fine cross-dialect) — only CREATE TABLE
    needs the substitution. NOTE: this copies column types only, not table
    constraints (UNIQUE, self-FK, CHECK) — the clone will accept rows the real
    Postgres schema would reject; extend it if a test needs those constraints
    enforced."""
    cols = [
        sa.Column(
            c.name,
            sa.JSON() if isinstance(c.type, (JSONB, ARRAY)) else c.type,
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
    _sqlite_ddl_table(Lesson.__tablename__, Lesson.__table__, ddl_metadata)
    _sqlite_ddl_table(Combo.__tablename__, Combo.__table__, ddl_metadata)
    async with engine.begin() as conn:
        await conn.run_sync(ddl_metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(db_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(rules_tools_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(lessons_tools_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(combos_tools_module, "get_session_factory", lambda: factory)
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


async def _seed_lesson(factory, **overrides) -> int:
    """Insert a Lesson row directly (bypassing add_lesson), for seeding fixed-status rows.
    tags defaults to None (not []): Lesson.tags is a Postgres ARRAY column with no generic
    SQLite bind-processor, so a Python list fails to bind against the test's SQLite engine.
    Uses a Core insert() rather than constructing the ORM object: the ORM's flush path
    applies the column's Python-side `default=list` whenever the attribute value is None
    (even when None was explicitly passed), which would re-introduce the list-bind failure;
    Core insert().values(...) binds exactly what is given."""
    defaults = dict(
        app=None,
        title="seed-lesson",
        content="seed content",
        tags=None,
        severity="INFO",
        source="ai-capture",
        status="approved",
        authority="informational",
        applicability={},
        version=1,
    )
    defaults.update(overrides)
    async with factory() as session:
        result = await session.execute(sa.insert(Lesson).values(**defaults).returning(Lesson.id))
        new_id = result.scalar_one()
        await session.commit()
        return new_id


async def _seed_combo(factory, **overrides) -> int:
    """Insert a Combo row directly (no add_combo tool exists), for seeding fixed-status rows.
    confirmed_in defaults to None (not []): Combo.confirmed_in is a Postgres ARRAY column with
    no generic SQLite bind-processor, so a Python list fails to bind against the test's SQLite
    engine. Uses a Core insert() for the same reason as _seed_lesson above."""
    defaults = dict(
        name="seed-combo",
        description="seed description",
        packages={},
        ecosystem="python",
        flavor=None,
        confirmed_in=None,
        status="approved",
        authority="informational",
        applicability={},
        version=1,
    )
    defaults.update(overrides)
    async with factory() as session:
        result = await session.execute(sa.insert(Combo).values(**defaults).returning(Combo.id))
        new_id = result.scalar_one()
        await session.commit()
        return new_id


async def test_search_lessons_excludes_deprecated_by_default(infra_db):
    await _seed_lesson(
        infra_db, title="dep-lesson", content="deprecated content", status="deprecated"
    )
    approved_id = await _seed_lesson(
        infra_db, title="live-lesson", content="live content", status="approved"
    )

    mcp = _infra_mcp()
    result = await mcp.call_tool("search_lessons", {"query": "content"})
    ids = {lesson["id"] for lesson in _data(result)["lessons"]}
    assert ids == {approved_id}


async def test_search_lessons_include_proposed_surfaces_proposed_lesson(infra_db):
    # Seeded directly (not via add_lesson): Lesson.tags is a Postgres ARRAY column that
    # add_lesson's `tags or []` default cannot bind against the test's SQLite engine (see
    # _seed_lesson's docstring). The behavior under test is search_lessons' status filter,
    # which is exercised identically regardless of how the row was inserted.
    new_id = await _seed_lesson(
        infra_db, title="prop-lesson", content="proposed content", status="proposed"
    )

    mcp = _infra_mcp()
    default = await mcp.call_tool("search_lessons", {"query": "proposed content"})
    assert new_id not in {lesson["id"] for lesson in _data(default)["lessons"]}

    with_proposed = await mcp.call_tool(
        "search_lessons", {"query": "proposed content", "include_proposed": True}
    )
    ids = {lesson["id"] for lesson in _data(with_proposed)["lessons"]}
    assert new_id in ids
    surfaced = next(lesson for lesson in _data(with_proposed)["lessons"] if lesson["id"] == new_id)
    assert surfaced["status"] == "proposed"


async def test_list_combos_excludes_deprecated_by_default(infra_db):
    await _seed_combo(infra_db, name="dep-combo", status="deprecated")
    approved_id = await _seed_combo(infra_db, name="live-combo", status="approved")

    mcp = _infra_mcp()
    result = await mcp.call_tool("list_combos", {})
    ids = {c["name"] for c in _data(result)["combos"]}
    assert "dep-combo" not in ids
    live = next(c for c in _data(result)["combos"] if c["name"] == "live-combo")
    assert live["status"] == "approved"
    assert approved_id  # sanity: seed succeeded


async def test_list_combos_include_proposed_includes_proposed_combo(infra_db):
    await _seed_combo(infra_db, name="proposed-combo", status="proposed")

    mcp = _infra_mcp()
    default = await mcp.call_tool("list_combos", {})
    assert "proposed-combo" not in {c["name"] for c in _data(default)["combos"]}

    with_proposed = await mcp.call_tool("list_combos", {"include_proposed": True})
    names = {c["name"] for c in _data(with_proposed)["combos"]}
    assert "proposed-combo" in names


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


# ---------------------------------------------------------------------------
# restore_rule approver gate (final-review FIX 1) — closes the
# propose -> delete -> restore self-approval bypass: RuleRepository.restore()
# unconditionally sets status=approved, so restore_rule itself must be gated
# behind the approver key, same as approve/reject/deprecate.
# ---------------------------------------------------------------------------


async def test_restore_rule_denied_without_approver_key(infra_db, monkeypatch):
    monkeypatch.setattr(rules_tools_module, "require_approver", lambda: False)
    seed_id = await _seed_rule(infra_db, rule="gated-restore-rule", status="deprecated")

    mcp = _infra_mcp()
    result = await mcp.call_tool("restore_rule", {"id": seed_id})
    body = _data(result)
    assert body == {"error": "not_authorized", "hint": "restore requires the approver key"}

    async with infra_db() as session:
        rule = await session.get(Rule, seed_id)
        assert rule.status == "deprecated"  # unchanged — restore never ran


async def test_restore_rule_succeeds_with_approver_key(infra_db, monkeypatch):
    monkeypatch.setattr(rules_tools_module, "require_approver", lambda: True)
    seed_id = await _seed_rule(infra_db, rule="approved-restore-rule", status="deprecated")

    mcp = _infra_mcp()
    result = await mcp.call_tool("restore_rule", {"id": seed_id})
    body = _data(result)
    assert body["restored"] is True

    async with infra_db() as session:
        rule = await session.get(Rule, seed_id)
        assert rule.status == "approved"


async def test_propose_delete_restore_cannot_reach_approved_without_approver_key(
    infra_db, monkeypatch
):
    """Full self-approval-bypass regression: a contributor-key caller can add_rule
    (-> proposed), but delete_rule is now also gated behind the approver key — the
    bypass chain is blocked one step earlier than before (propose->delete->restore).
    restore_rule remains gated too, as a defense-in-depth check."""
    monkeypatch.setattr(rules_tools_module, "require_approver", lambda: False)

    mcp = _infra_mcp()
    added = await mcp.call_tool(
        "add_rule",
        {
            "severity": "WARN",
            "category": "deployment",
            "rule": "bypass-attempt-rule",
            "reason": "self-approval attempt",
        },
    )
    new_id = _data(added)["id"]
    assert _data(added)["status"] == "proposed"

    deleted = await mcp.call_tool("delete_rule", {"id": new_id})
    assert _data(deleted)["error"] == "not_authorized"

    restored = await mcp.call_tool("restore_rule", {"id": new_id})
    assert _data(restored)["error"] == "not_authorized"

    async with infra_db() as session:
        rule = await session.get(Rule, new_id)
        assert rule.status == "proposed"  # never reached approved; delete itself was blocked


# ---------------------------------------------------------------------------
# delete_rule approver gate (WS-1.4 spec §5.6) — delete_rule is now gated
# unconditionally behind the approver key, same pattern as restore_rule.
# ---------------------------------------------------------------------------


async def test_delete_rule_denied_without_approver_key(infra_db, monkeypatch):
    monkeypatch.setattr(rules_tools_module, "require_approver", lambda: False)
    seed_id = await _seed_rule(infra_db, rule="gated-delete-rule")

    mcp = _infra_mcp()
    result = await mcp.call_tool("delete_rule", {"id": seed_id})
    body = _data(result)
    assert body == {"error": "not_authorized", "hint": "delete requires the approver key"}

    async with infra_db() as session:
        rule = await session.get(Rule, seed_id)
        assert rule.status == "approved"  # unchanged — retire never ran
        assert rule.retired_at is None


async def test_delete_rule_succeeds_with_approver_key(infra_db, monkeypatch):
    monkeypatch.setattr(rules_tools_module, "require_approver", lambda: True)
    seed_id = await _seed_rule(infra_db, rule="approved-delete-rule")

    mcp = _infra_mcp()
    result = await mcp.call_tool("delete_rule", {"id": seed_id})
    body = _data(result)
    assert body["retired"] is True

    async with infra_db() as session:
        rule = await session.get(Rule, seed_id)
        assert rule.status == "deprecated"
        assert rule.retired_at is not None


# ---------------------------------------------------------------------------
# update_rule approver gate (WS-1.4 spec §5.6) — updating a required-authority
# rule requires the approver key; informational/recommended rules can still be
# updated by a contributor key (no regression).
# ---------------------------------------------------------------------------


async def test_update_rule_on_required_rule_denied_without_approver_key(infra_db, monkeypatch):
    monkeypatch.setattr(rules_tools_module, "require_approver", lambda: False)
    seed_id = await _seed_rule(
        infra_db, rule="required-update-rule", authority="required", reason="original"
    )

    mcp = _infra_mcp()
    result = await mcp.call_tool("update_rule", {"id": seed_id, "reason": "changed"})
    body = _data(result)
    assert body == {
        "error": "not_authorized",
        "hint": "updating a required-authority rule requires the approver key",
    }

    async with infra_db() as session:
        rule = await session.get(Rule, seed_id)
        assert rule.reason == "original"  # unchanged — update never applied


async def test_update_rule_on_required_rule_succeeds_with_approver_key(infra_db, monkeypatch):
    monkeypatch.setattr(rules_tools_module, "require_approver", lambda: True)
    seed_id = await _seed_rule(
        infra_db, rule="required-update-rule-2", authority="required", reason="original"
    )

    mcp = _infra_mcp()
    result = await mcp.call_tool("update_rule", {"id": seed_id, "reason": "changed"})
    body = _data(result)
    assert body["updated"] is True

    async with infra_db() as session:
        rule = await session.get(Rule, seed_id)
        assert rule.reason == "changed"


async def test_update_rule_on_non_required_rule_succeeds_without_approver_key(
    infra_db, monkeypatch
):
    """No regression: informational/recommended rules keep working for a contributor key."""
    monkeypatch.setattr(rules_tools_module, "require_approver", lambda: False)
    seed_id = await _seed_rule(
        infra_db, rule="recommended-update-rule", authority="recommended", reason="original"
    )

    mcp = _infra_mcp()
    result = await mcp.call_tool("update_rule", {"id": seed_id, "reason": "changed"})
    body = _data(result)
    assert body["updated"] is True

    async with infra_db() as session:
        rule = await session.get(Rule, seed_id)
        assert rule.reason == "changed"


# ---------------------------------------------------------------------------
# Seeder governance stamp (final-review FIX 2) — seed.py inserts rules/combos/
# lessons WITHOUT governance fields would otherwise land at the server default
# status='proposed', invisible to every read tool on a fresh DB. Seed data is
# curated, so it must land approved/informational.
#
# Exercised by running the real seed() function against fake repos/session
# that capture what was passed, rather than a live SQLite DB: Version and
# Combo both carry a Postgres ARRAY(Text) column (confirmed_in) which has no
# generic SQLite bind processor (see _seed_lesson/_seed_combo's docstrings in
# the governance test block above for the same pre-existing limitation) — a
# real data.json row with a populated confirmed_in list cannot be inserted
# against SQLite via the ORM at all, independent of this fix. Capturing the
# actual dicts seed() builds and passes to the repositories/session exercises
# the exact code path under test without touching that unrelated limitation.
# ---------------------------------------------------------------------------


class _FakeSeedSession:
    def __init__(self):
        self.added: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        pass


async def test_seed_lands_rules_combos_lessons_as_approved(monkeypatch):
    captured_rules: list[dict] = []
    captured_lessons: list[dict] = []
    fake_session = _FakeSeedSession()

    class _FakeRuleRepo:
        def __init__(self, session):
            self.session = session

        async def add_if_not_exists(self, data):
            captured_rules.append(data)

    class _FakeLessonRepo:
        def __init__(self, session):
            self.session = session

        async def add_if_not_exists(self, data):
            captured_lessons.append(data)

    class _FakeComboRepo:
        def __init__(self, session):
            self.session = session

        async def get_by_name(self, name):
            return None  # every combo is "new" -> exercises the insert branch

    class _FakeVersionRepo:
        def __init__(self, session):
            self.session = session

        async def get_by_package(self, package):
            return None

        async def upsert(self, data):
            pass  # versions are ungoverned; untouched by this fix

    monkeypatch.setattr(infra_seed_module, "get_session_factory", lambda: lambda: fake_session)
    monkeypatch.setattr(infra_seed_module, "RuleRepository", _FakeRuleRepo)
    monkeypatch.setattr(infra_seed_module, "LessonRepository", _FakeLessonRepo)
    monkeypatch.setattr(infra_seed_module, "ComboRepository", _FakeComboRepo)
    monkeypatch.setattr(infra_seed_module, "VersionRepository", _FakeVersionRepo)

    await infra_seed_module.seed(skip_existing=True)

    assert captured_rules, "seed must insert at least one rule"
    assert captured_lessons, "seed must insert at least one lesson"
    combos_added = [obj for obj in fake_session.added if isinstance(obj, Combo)]
    assert combos_added, "seed must insert at least one combo"

    for d in captured_rules:
        assert d["status"] == "approved" and d["authority"] == "informational"
        assert d["proposed_by"] == "seed" and d["reviewed_by"] == "seed"
        assert d["reviewed_at"] is not None
    for d in captured_lessons:
        assert d["status"] == "approved" and d["authority"] == "informational"
        assert d["proposed_by"] == "seed" and d["reviewed_by"] == "seed"
        assert d["reviewed_at"] is not None
    for c in combos_added:
        assert c.status == "approved" and c.authority == "informational"
        assert c.proposed_by == "seed" and c.reviewed_by == "seed"
        assert c.reviewed_at is not None


async def test_seed_governance_dicts_include_governance_keys():
    """Belt-and-suspenders on the stamping helper itself: every governed record
    type's insert dict carries the full governance stamp."""
    stamp = infra_seed_module._seed_governance()
    assert stamp["status"] == "approved"
    assert stamp["authority"] == "informational"
    assert stamp["proposed_by"] == "seed"
    assert stamp["reviewed_by"] == "seed"
    assert stamp["reviewed_at"] is not None
