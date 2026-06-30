"""Tests for the code brain package: capabilities, tool registration, REST surface, seed data.

Mirrors tests/brains/test_infra.py — the code brain is modeled directly on infra.
"""
import datetime
import json
from pathlib import Path

import httpx
from fastmcp import FastMCP

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
    from src.core.db import Base as CoreBase
    from src.brains.code import models

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
