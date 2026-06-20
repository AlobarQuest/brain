"""Tests for the infra brain package: capabilities, tool registration, and registry guard."""
import importlib

import pytest
from fastmcp import FastMCP

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
