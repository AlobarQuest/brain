"""Tests for the code brain package: capabilities, tool registration, REST surface, seed data.

Mirrors tests/brains/test_infra.py — the code brain is modeled directly on infra.
"""
import datetime
import json
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from fastmcp import FastMCP
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import src.brains.code.tools.exemplars as exemplars_tools_module
import src.brains.code.tools.lessons as lessons_tools_module
import src.brains.code.tools.roads as roads_tools_module
import src.brains.code.tools.rules as rules_tools_module
import src.brains.code.tools.search as search_tools_module
import src.core.db as db_module
import src.core.governance as governance_module
from src.brains.code.models import Exemplar, Lesson, Road, Rule
from src.core.config import BrainType
from src.core.registry import Capabilities, load_brain

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_code_capabilities():
    brain = load_brain(BrainType.CODE)
    assert brain.capabilities.embeddings is False
    assert brain.capabilities.auth_exact == ("/api/health",)
    assert brain.capabilities.auth_prefixes == ()


def test_code_capabilities_is_correct_type():
    brain = load_brain(BrainType.CODE)
    assert isinstance(brain.capabilities, Capabilities)


def test_code_no_embeddings():
    """No pgvector for v1 — match infra brain (embeddings=False)."""
    brain = load_brain(BrainType.CODE)
    assert brain.capabilities.embeddings is False


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    # roads
    "list_roads",
    "get_road",
    "add_road",
    "update_road",
    # rules
    "get_rules",
    "add_rule",
    "retire_rule",
    # lessons
    "add_lesson",
    # exemplars
    "add_exemplar",
    # search
    "search",
    # governance
    "approve",
    "reject",
    "deprecate",
}


async def test_code_registers_all_tools():
    brain = load_brain(BrainType.CODE)
    mcp = FastMCP("t")
    brain.register(mcp)
    registered = {t.name for t in await mcp.list_tools()}
    assert EXPECTED_TOOLS == registered


async def test_code_register_is_idempotent_on_fresh_mcp():
    brain = load_brain(BrainType.CODE)
    mcp1, mcp2 = FastMCP("a"), FastMCP("b")
    brain.register(mcp1)
    brain.register(mcp2)
    assert {t.name for t in await mcp1.list_tools()} == EXPECTED_TOOLS
    assert {t.name for t in await mcp2.list_tools()} == EXPECTED_TOOLS


# ---------------------------------------------------------------------------
# REST lookup API — read endpoints (auth-protected by the core middleware)
# ---------------------------------------------------------------------------


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeRule:
    id = 1
    road_slug = "meta-discipline"
    severity = "INFO"
    category = "quality"
    rule = "Query Code Brain before implementing a cross-cutting pattern."
    reason = "Code Brain is the source of truth."
    source = None
    check = None
    good_example = None
    bad_example = None
    retired_at = None
    created_at = datetime.datetime(2026, 1, 2, 3, 4, 5)
    status = "approved"
    authority = "informational"
    applicability = {"road_slug": "meta-discipline", "category": "quality"}
    conflict_kind = None


class _FakeRoad:
    id = 1
    slug = "error-logging"
    name = "Error handling & structured logging"
    category = "application"
    status = "unpaved"
    summary = "How we log errors."
    decided_approach = None
    home = "high-value gap"
    owner_standard = None
    adr_ref = None
    last_validated_at = None
    validation_note = None
    created_at = datetime.datetime(2026, 1, 2, 3, 4, 5)
    updated_at = datetime.datetime(2026, 1, 2, 3, 4, 5)


class _FakeRuleRepo:
    def __init__(self, session):
        pass

    async def list_all(self, **kw):
        return [_FakeRule()]


class _FakeRoadRepo:
    def __init__(self, session):
        pass

    async def list_all(self, **kw):
        return [_FakeRoad()]


def _code_app(monkeypatch):
    """Build the real code app with the DB layer mocked out."""
    monkeypatch.setenv("BRAIN_TYPE", "code")
    monkeypatch.setenv("MCP_ACCESS_KEY", "a" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("POSTGRES_HOST", "x")
    monkeypatch.setenv("POSTGRES_USER", "x")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("POSTGRES_DB", "x")

    import src.brains.code as code
    monkeypatch.setattr(code, "get_session_factory", lambda: (lambda: _FakeSession()))
    monkeypatch.setattr(code, "RuleRepository", _FakeRuleRepo)
    monkeypatch.setattr(code, "RoadRepository", _FakeRoadRepo)

    from src.core.app import create_app
    return create_app()


async def test_rest_routes_registered(monkeypatch):
    app = _code_app(monkeypatch)
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/api/rules" in paths
    assert "/api/roads" in paths


async def test_api_rules_requires_key(monkeypatch):
    app = _code_app(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/rules")
    assert resp.status_code == 401


async def test_api_rules_returns_rules_with_key(monkeypatch):
    app = _code_app(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/rules?key=" + "a" * 64)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["rules"]) == 1
    r = body["rules"][0]
    assert r["severity"] == "INFO"
    assert r["road_slug"] == "meta-discipline"
    # created_at is the REST-only field (the MCP get_rules tool omits it).
    assert r["created_at"] == "2026-01-02T03:04:05"


async def test_api_roads_returns_roads_with_key(monkeypatch):
    app = _code_app(monkeypatch)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/roads?key=" + "a" * 64)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["roads"]) == 1
    assert body["roads"][0]["slug"] == "error-logging"
    assert body["roads"][0]["status"] == "unpaved"


# ---------------------------------------------------------------------------
# Model metadata — the four tables live in the code brain's own metadata
# ---------------------------------------------------------------------------

def test_models_define_four_tables():
    from src.brains.code import models

    tables = set(models.Base.metadata.tables)
    assert {"roads", "rules", "lessons", "exemplars"} <= tables


def test_code_models_use_isolated_metadata():
    """Code brain must NOT share core.db.Base, or its `rules`/`lessons` tables
    collide with infra brain's same-named tables in one process."""
    from src.brains.code import models
    from src.core.db import Base as CoreBase

    assert models.Base is not CoreBase


# ---------------------------------------------------------------------------
# Seed data — the two discipline rules + every road from the Golden Paths board
# ---------------------------------------------------------------------------

VALID_CATEGORIES = {
    "application", "data", "api", "frontend",
    "delivery-ops", "quality", "security", "ai",
}
VALID_STATUSES = {"paved", "partial", "unpaved", "paving"}


def _seed_data():
    path = Path("src/brains/code/seed/data.json")
    return json.loads(path.read_text())


def test_seed_imports_every_board_road_plus_meta():
    data = _seed_data()
    roads = data["roads"]
    slugs = [r["slug"] for r in roads]
    assert len(slugs) == len(set(slugs)), "road slugs must be unique"
    # 50 roads from the board + 1 meta-discipline road.
    non_meta = [s for s in slugs if s != "meta-discipline"]
    assert len(non_meta) == 50
    assert "meta-discipline" in slugs


def test_seed_roads_have_valid_enums_and_required_fields():
    for r in _seed_data()["roads"]:
        assert r["slug"] and r["name"] and r["summary"]
        assert r["category"] in VALID_CATEGORIES, r
        assert r["status"] in VALID_STATUSES, r


def test_seed_representative_roads_present_with_correct_status():
    by_slug = {r["slug"]: r for r in _seed_data()["roads"]}
    assert by_slug["auth"]["status"] == "paved"
    assert by_slug["error-logging"]["status"] == "unpaved"
    assert by_slug["backups-restore"]["status"] == "paved"
    assert by_slug["testing-strategy"]["status"] == "partial"
    assert by_slug["meta-discipline"]["category"] == "quality"


def test_parse_ts_accepts_iso_and_z_and_rejects_garbage():
    from datetime import datetime

    from src.brains.code.tools.roads import _parse_ts

    dt = _parse_ts("2026-06-30T12:00:00Z")
    assert isinstance(dt, datetime) and dt.tzinfo is not None
    assert _parse_ts("2026-06-30T12:00:00+00:00") is not None
    assert _parse_ts("not-a-date") is None


class _FakeRoadRepoMissing:
    def __init__(self, session):
        pass

    async def get_by_slug(self, slug):
        return None


async def test_api_road_missing_returns_404(monkeypatch):
    app = _code_app(monkeypatch)
    import src.brains.code as code
    monkeypatch.setattr(code, "RoadRepository", _FakeRoadRepoMissing)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        resp = await c.get("/api/road/does-not-exist?key=" + "a" * 64)
    assert resp.status_code == 404


def test_seed_has_two_discipline_rules():
    rules = _seed_data()["rules"]
    assert len(rules) == 2
    for r in rules:
        assert r["severity"] == "INFO"
        assert r["road_slug"] == "meta-discipline"
        assert r["category"] == "quality"
    blob = json.dumps(rules).lower()
    assert "before" in blob  # query-first discipline
    assert "write back" in blob  # write-back discipline


# ---------------------------------------------------------------------------
# Governance (WS-1.4): propose-only writes, safe-retrieval filters, conflicts.
# Road is EXCLUDED (organizational catalog, not authoritative knowledge — its
# own paving `status` collides with the governance lifecycle `status`).
#
# Exercised against a real in-memory SQLite DB (not fakes) so find_conflicts'
# actual queries run — mirrors tests/brains/test_infra.py's async-SQLite pattern.
# ---------------------------------------------------------------------------


def _sqlite_ddl_table(name: str, orm_table: sa.FromClause, metadata: sa.MetaData) -> sa.Table:
    """Clone an ORM table's columns onto a throwaway MetaData for the SQLite test DDL,
    substituting Postgres-only types (JSONB, ARRAY) for portable JSON (SQLite's DDL compiler
    cannot render either). See tests/brains/test_infra.py's identical helper for the full
    rationale — this copies column types only, not table constraints (UNIQUE, self-FK, CHECK)."""
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
    """Unwrap a mcp.call_tool() ToolResult's structured_content, which is typed Optional
    even though every code-brain tool returns a dict."""
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
async def code_db(monkeypatch):
    """A real async-SQLite engine wired into the code brain's tool modules (and
    governance.py's lazily-imported get_session_factory), so governance write tools and
    find_conflicts run their actual queries end to end."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    ddl_metadata = sa.MetaData()
    _sqlite_ddl_table(Road.__tablename__, Road.__table__, ddl_metadata)
    _sqlite_ddl_table(Rule.__tablename__, Rule.__table__, ddl_metadata)
    _sqlite_ddl_table(Lesson.__tablename__, Lesson.__table__, ddl_metadata)
    _sqlite_ddl_table(Exemplar.__tablename__, Exemplar.__table__, ddl_metadata)
    async with engine.begin() as conn:
        await conn.run_sync(ddl_metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(db_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(roads_tools_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(rules_tools_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(lessons_tools_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(exemplars_tools_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(search_tools_module, "get_session_factory", lambda: factory)
    yield factory
    await engine.dispose()


def _code_mcp() -> FastMCP:
    brain = load_brain(BrainType.CODE)
    mcp = FastMCP("t")
    brain.register(mcp)
    return mcp


async def _seed_road(factory, **overrides) -> str:
    """Insert a Road row directly, for seeding the parent a Rule/Lesson/Exemplar FK needs."""
    defaults = dict(
        slug="test-road", name="Test Road", category="quality", status="paved",
        summary="test road", decided_approach=None, home=None, owner_standard=None,
        adr_ref=None,
    )
    defaults.update(overrides)
    async with factory() as session:
        session.add(Road(**defaults))
        await session.commit()
    return str(defaults["slug"])


async def _seed_rule(factory, **overrides) -> int:
    """Insert a Rule row directly (bypassing add_rule), for seeding an already-approved
    conflict target or a fixed-status row."""
    defaults = dict(
        road_slug="test-road", category="security", severity="BLOCK",
        rule="seed-rule", reason="seed", source=None, check=None,
        good_example=None, bad_example=None,
        status="approved", authority="informational", applicability={}, version=1,
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
    SQLite bind-processor, so a Python list fails to bind against the test's SQLite engine
    (see tests/brains/test_infra.py's identical helper). Uses a Core insert() rather than
    constructing the ORM object for the same reason."""
    defaults = dict(
        road_slug="test-road", title="seed-lesson", content="seed content", tags=None,
        source_app=None,
        status="approved", authority="informational", applicability={}, version=1,
    )
    defaults.update(overrides)
    async with factory() as session:
        result = await session.execute(sa.insert(Lesson).values(**defaults).returning(Lesson.id))
        new_id = result.scalar_one()
        await session.commit()
        return new_id


async def _seed_exemplar(factory, **overrides) -> int:
    """Insert an Exemplar row directly (bypassing add_exemplar), for seeding fixed-status rows."""
    defaults = dict(
        road_slug="test-road", label="seed-exemplar", location="path/to/file", note=None,
        status="approved", authority="informational", applicability={}, version=1,
    )
    defaults.update(overrides)
    async with factory() as session:
        exemplar = Exemplar(**defaults)
        session.add(exemplar)
        await session.commit()
        await session.refresh(exemplar)
        return exemplar.id


async def test_add_rule_is_proposed_by_default(code_db):
    await _seed_road(code_db, slug="road-a")
    mcp = _code_mcp()
    result = await mcp.call_tool(
        "add_rule",
        {
            "road_slug": "road-a", "severity": "WARN", "category": "quality",
            "rule": "r1", "reason": "why",
        },
    )
    body = _data(result)
    assert body["created"] is True
    assert body["status"] == "proposed"
    assert body["conflict"] is None
    async with code_db() as session:
        rule = await session.get(Rule, body["id"])
        assert rule.status == "proposed"
        assert rule.authority == "informational"


class _FakeCommitSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        return None


class _FoundRoadRepo:
    def __init__(self, session):
        pass

    async def get_by_slug(self, slug):
        return object()  # any truthy value signals "road exists"


class _RecordingLessonRepo:
    def __init__(self, session):
        pass

    async def add(self, data):
        return type("Rec", (), {"id": 99, "title": data["title"], "status": data["status"]})()


async def test_add_lesson_is_proposed_by_default(monkeypatch):
    """Verified via fake repositories rather than the real code_db SQLite fixture:
    Lesson.tags is a Postgres ARRAY column with no generic SQLite bind-processor, so
    add_lesson's `tags or []` default cannot bind against a real SQLite engine (same root
    cause tests/brains/test_infra.py's _seed_lesson helper documents). This isolates the
    propose-only wiring under test from that pre-existing, Postgres-only-in-prod SQLite test
    limitation."""
    monkeypatch.setattr(
        lessons_tools_module, "get_session_factory", lambda: (lambda: _FakeCommitSession())
    )
    monkeypatch.setattr(lessons_tools_module, "RoadRepository", _FoundRoadRepo)
    monkeypatch.setattr(lessons_tools_module, "LessonRepository", _RecordingLessonRepo)

    mcp = _code_mcp()
    result = await mcp.call_tool(
        "add_lesson", {"title": "L1", "content": "content", "road_slug": "road-b"}
    )
    body = _data(result)
    assert body["created"] is True
    assert body["status"] == "proposed"


async def test_add_exemplar_is_proposed_by_default(code_db):
    await _seed_road(code_db, slug="road-c")
    mcp = _code_mcp()
    result = await mcp.call_tool(
        "add_exemplar", {"road_slug": "road-c", "label": "E1", "location": "path/to/file"}
    )
    body = _data(result)
    assert body["created"] is True
    assert body["status"] == "proposed"
    async with code_db() as session:
        exemplar = await session.get(Exemplar, body["id"])
        assert exemplar.status == "proposed"
        assert exemplar.authority == "informational"


async def test_get_rules_default_excludes_proposed_include_proposed_includes(code_db):
    await _seed_road(code_db, slug="road-d")
    mcp = _code_mcp()
    added = await mcp.call_tool(
        "add_rule",
        {
            "road_slug": "road-d", "severity": "WARN", "category": "quality",
            "rule": "r2", "reason": "why",
        },
    )
    new_id = _data(added)["id"]

    default = await mcp.call_tool("get_rules", {})
    assert new_id not in {r["id"] for r in _data(default)["rules"]}

    with_proposed = await mcp.call_tool("get_rules", {"include_proposed": True})
    ids = {r["id"] for r in _data(with_proposed)["rules"]}
    assert new_id in ids


async def test_get_rules_min_authority_filters(code_db):
    await _seed_road(code_db, slug="road-e")
    required_id = await _seed_rule(
        code_db, road_slug="road-e", rule="required-rule", authority="required"
    )
    await _seed_rule(
        code_db, road_slug="road-e", rule="informational-rule", authority="informational"
    )

    mcp = _code_mcp()
    result = await mcp.call_tool("get_rules", {"min_authority": "required"})
    ids = {r["id"] for r in _data(result)["rules"]}
    assert ids == {required_id}


async def test_get_road_hides_deprecated_embedded_rule_by_default(code_db):
    """A deprecated embedded rule is hidden by default AND stays hidden even with
    include_proposed=True — include_proposed only widens to [approved, proposed], never
    deprecated/superseded."""
    await _seed_road(code_db, slug="road-f")
    approved_id = await _seed_rule(
        code_db, road_slug="road-f", rule="approved-rule", status="approved"
    )
    deprecated_id = await _seed_rule(
        code_db, road_slug="road-f", rule="deprecated-rule", status="deprecated"
    )

    mcp = _code_mcp()
    default = await mcp.call_tool("get_road", {"slug": "road-f"})
    assert {r["id"] for r in _data(default)["rules"]} == {approved_id}

    with_proposed = await mcp.call_tool("get_road", {"slug": "road-f", "include_proposed": True})
    ids = {r["id"] for r in _data(with_proposed)["rules"]}
    assert ids == {approved_id}
    assert deprecated_id not in ids


async def test_get_road_include_proposed_surfaces_proposed_lesson_and_exemplar(code_db):
    await _seed_road(code_db, slug="road-g")
    proposed_lesson_id = await _seed_lesson(
        code_db, road_slug="road-g", title="prop-lesson", status="proposed"
    )
    proposed_exemplar_id = await _seed_exemplar(
        code_db, road_slug="road-g", label="prop-exemplar", status="proposed"
    )

    mcp = _code_mcp()
    default = await mcp.call_tool("get_road", {"slug": "road-g"})
    assert proposed_lesson_id not in {ln["id"] for ln in _data(default)["lessons"]}
    assert proposed_exemplar_id not in {e["id"] for e in _data(default)["exemplars"]}

    with_proposed = await mcp.call_tool("get_road", {"slug": "road-g", "include_proposed": True})
    assert proposed_lesson_id in {ln["id"] for ln in _data(with_proposed)["lessons"]}
    assert proposed_exemplar_id in {e["id"] for e in _data(with_proposed)["exemplars"]}


async def test_get_road_min_authority_filters_embedded_rules(code_db):
    await _seed_road(code_db, slug="road-g2")
    required_id = await _seed_rule(
        code_db, road_slug="road-g2", rule="road-g2-required", authority="required"
    )
    await _seed_rule(
        code_db, road_slug="road-g2", rule="road-g2-informational", authority="informational"
    )

    mcp = _code_mcp()
    result = await mcp.call_tool("get_road", {"slug": "road-g2", "min_authority": "required"})
    assert {r["id"] for r in _data(result)["rules"]} == {required_id}


async def test_search_excludes_deprecated_rule_and_lesson_by_default(code_db):
    await _seed_road(code_db, slug="road-h")
    await _seed_rule(
        code_db, road_slug="road-h", rule="deprecated search rule",
        reason="findme", status="deprecated",
    )
    approved_rule_id = await _seed_rule(
        code_db, road_slug="road-h", rule="approved search rule",
        reason="findme", status="approved",
    )
    await _seed_lesson(
        code_db, road_slug="road-h", title="deprecated search lesson",
        content="findme", status="deprecated",
    )
    approved_lesson_id = await _seed_lesson(
        code_db, road_slug="road-h", title="approved search lesson",
        content="findme", status="approved",
    )

    mcp = _code_mcp()
    result = await mcp.call_tool("search", {"query": "findme"})
    data = _data(result)
    assert {r["id"] for r in data["rules"]} == {approved_rule_id}
    assert {ln["id"] for ln in data["lessons"]} == {approved_lesson_id}


async def test_add_rule_duplicate_conflict_blocks_auto_approve_until_acknowledged(
    code_db, monkeypatch
):
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    await _seed_road(code_db, slug="road-i")
    chk = {"kind": "forbidden_pattern", "pattern": "P"}
    seed_id = await _seed_rule(
        code_db, road_slug="road-i", rule="required-rule-base", category="security",
        authority="required", check=chk,
    )

    mcp = _code_mcp()
    added = await mcp.call_tool(
        "add_rule",
        {
            "road_slug": "road-i", "severity": "BLOCK", "category": "security",
            "rule": "required-rule-dup", "reason": "candidate", "check": chk,
            "proposed_by": "agent-x", "auto_approve": True,
        },
    )
    body = _data(added)
    assert body["conflict"] == "duplicate"
    assert body["status"] == "proposed"  # duplicate cancels auto-approve
    new_id = body["id"]

    async with code_db() as session:
        new_rule = await session.get(Rule, new_id)
        assert new_rule.conflict_kind == "duplicate"
        assert new_rule.status == "proposed"
        seed_rule = await session.get(Rule, seed_id)
        assert seed_rule.status == "approved"
        assert seed_rule.conflict_kind is None  # the matched target row is never mutated

    no_ack = await mcp.call_tool("approve", {"record_type": "rule", "id": new_id})
    assert _data(no_ack)["error"] == "conflict_unacknowledged"

    with_ack = await mcp.call_tool(
        "approve", {"record_type": "rule", "id": new_id, "acknowledge_conflict": True}
    )
    assert _data(with_ack)["approved"] is True
    assert _data(with_ack)["status"] == "approved"

    async with code_db() as session:
        approved_rule = await session.get(Rule, new_id)
        assert approved_rule.status == "approved"
        assert approved_rule.conflict_acknowledged_at is not None
        assert approved_rule.conflict_note is not None  # preserved, not cleared


async def test_add_rule_judgment_check_never_flagged_as_duplicate(code_db, monkeypatch):
    """A judgment-kind check is opaque (no fixed expected shape) — even a byte-identical
    judgment check on an approved required rule must never be flagged as a Layer-1 duplicate.
    Category differs from the seed on purpose so the Layer-2 overlap signature
    (road_slug, category) also misses — isolating the duplicate-exclusion behavior alone."""
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    await _seed_road(code_db, slug="road-j")
    chk = {"kind": "judgment", "prompt": "Does this violate the spirit of X?"}
    await _seed_rule(
        code_db, road_slug="road-j", rule="judgment-rule-base", category="quality",
        authority="required", check=chk,
    )

    mcp = _code_mcp()
    added = await mcp.call_tool(
        "add_rule",
        {
            "road_slug": "road-j", "severity": "WARN", "category": "testing",
            "rule": "judgment-rule-dup", "reason": "candidate", "check": chk,
            "proposed_by": "agent-x", "auto_approve": True,
        },
    )
    body = _data(added)
    assert body["conflict"] is None  # judgment checks are opaque, never a duplicate
    assert body["status"] == "approved"  # auto-approve succeeds unimpeded


async def test_add_rule_judgment_check_can_still_flag_overlap_on_matching_applicability(
    code_db, monkeypatch
):
    """Layer-2 overlap is based on applicability (road_slug, category) alone, independent of
    check kind — a judgment check is excluded from Layer-1 duplicate detection only."""
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    await _seed_road(code_db, slug="road-j2")
    chk = {"kind": "judgment", "prompt": "Does this violate the spirit of X?"}
    await _seed_rule(
        code_db, road_slug="road-j2", rule="judgment-rule-base-2", category="quality",
        authority="required", check=chk,
    )

    mcp = _code_mcp()
    added = await mcp.call_tool(
        "add_rule",
        {
            "road_slug": "road-j2", "severity": "WARN", "category": "quality",
            "rule": "judgment-rule-dup-2", "reason": "candidate", "check": chk,
            "proposed_by": "agent-x", "auto_approve": True,
        },
    )
    body = _data(added)
    assert body["conflict"] == "overlap"
    assert body["status"] == "approved"  # overlap is advisory, never blocks auto-approve


async def test_add_road_still_works_unchanged(code_db):
    """Road is excluded from governance: add_road stays direct-write (not propose-only)
    and the road carries no governance columns."""
    mcp = _code_mcp()
    result = await mcp.call_tool(
        "add_road",
        {
            "slug": "road-k", "name": "Road K", "category": "quality",
            "status": "unpaved", "summary": "a road",
        },
    )
    body = _data(result)
    assert body["created"] is True
    assert body["slug"] == "road-k"
    assert "status" not in body or body["status"] == "unpaved"  # paving status, not governance

    async with code_db() as session:
        result = await session.execute(sa.select(Road).where(Road.slug == "road-k"))
        road = result.scalar_one()
        assert road.status == "unpaved"
        assert not hasattr(road, "authority")
        assert not hasattr(road, "conflict_kind")
