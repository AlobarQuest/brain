"""Retired-address guard on Infra Brain writes, and the public address route."""

import json

import httpx
import pytest
from fastmcp import FastMCP
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import src.brains.infra as infra_module
import src.brains.infra.services.proposals as proposals_module
import src.brains.infra.tools.lessons as lessons_tools_module
import src.brains.infra.tools.rules as rules_tools_module
import src.core.governance as governance_module
from src.brains.infra.models import Environment, Rule
from src.brains.infra.repositories.environments import EnvironmentRepository
from src.core.address_guard import (
    book_from_payload,
    payload_from_environments,
)
from src.core.config import BrainType
from src.core.registry import load_brain

RETIRED = "178.156.247.239"
CURRENT = "40.160.102.182"
BOOK = book_from_payload(
    {
        "roles": [
            {
                "role": "prod-vps",
                "current": [CURRENT],
                "retired": [RETIRED, "100.112.188.96", "ubuntu-2gb-ash-1"],
            }
        ]
    }
)


@pytest.fixture(autouse=True)
def fixed_book(monkeypatch):
    async def book(self):
        return BOOK

    monkeypatch.setattr(EnvironmentRepository, "address_book", book)


def _mcp() -> FastMCP:
    mcp = FastMCP("t")
    load_brain(BrainType.INFRA).register(mcp)
    return mcp


def _data(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def commit(self):
        return None


class _RecordingRepo:
    added: list[dict] = []

    def __init__(self, session):
        pass

    async def add(self, data):
        type(self).added.append(data)
        return type(
            "Rec",
            (),
            {
                "id": 1,
                "title": data.get("title"),
                "severity": data.get("severity"),
                "category": data.get("category"),
                "status": data["status"],
            },
        )()


@pytest.fixture
def fake_repos(monkeypatch):
    _RecordingRepo.added = []
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    for module in (lessons_tools_module, rules_tools_module):
        monkeypatch.setattr(module, "get_session_factory", lambda: lambda: _Session())
    monkeypatch.setattr(lessons_tools_module, "LessonRepository", _RecordingRepo)
    monkeypatch.setattr(rules_tools_module, "RuleRepository", _RecordingRepo)

    async def no_conflicts(*a, **k):
        return None

    monkeypatch.setattr(rules_tools_module, "find_conflicts", no_conflicts)
    return _RecordingRepo


async def test_add_lesson_retired_held_even_with_approver_and_auto_approve(fake_repos):
    result = _data(
        await _mcp().call_tool(
            "add_lesson",
            {
                "title": "Deploy",
                "content": f"ssh root@{RETIRED} to restart",
                "severity": "WARN",
                "auto_approve": True,
            },
        )
    )
    assert result["status"] == "proposed"
    assert fake_repos.added[0]["conflict_kind"] == "retired_address"
    assert RETIRED in json.dumps(result)
    assert "prod-vps" in json.dumps(result)


async def test_add_lesson_current_address_saves_with_hint(fake_repos):
    result = _data(
        await _mcp().call_tool(
            "add_lesson",
            {"title": "Deploy", "content": f"host {CURRENT}", "severity": "WARN"},
        )
    )
    assert result["status"] == "approved"
    assert "hint" in result and "prod-vps" in result["hint"]
    assert "retired_address" not in result


async def test_add_rule_retired_in_check_json_held(fake_repos):
    result = _data(
        await _mcp().call_tool(
            "add_rule",
            {
                "severity": "WARN",
                "category": "deploy",
                "rule": "Use the host",
                "reason": "because",
                "check": {"host": RETIRED},
                "auto_approve": True,
            },
        )
    )
    assert result["status"] == "proposed"
    assert result["conflict"] == "retired_address"
    assert result["retired_address"] == [{"match": RETIRED, "role": "prod-vps"}]


async def _rule_db(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    async with engine.begin() as conn:
        await conn.run_sync(Rule.__table__.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(rules_tools_module, "get_session_factory", lambda: factory)
    async with factory() as session:
        rule = Rule(
            severity="WARN",
            category="deploy",
            rule="r",
            reason="orig",
            status="approved",
            authority="informational",
            applicability={},
            version=1,
        )
        session.add(rule)
        await session.commit()
        return factory, rule.id, engine


async def test_update_rule_retired_refused(monkeypatch):
    factory, rule_id, engine = await _rule_db(monkeypatch)
    result = _data(
        await _mcp().call_tool("update_rule", {"id": rule_id, "reason": f"moved from {RETIRED}"})
    )
    assert result["error"] == "retired_address"
    assert result["matches"] == [{"match": RETIRED, "role": "prod-vps"}]
    async with factory() as session:
        assert (await session.get(Rule, rule_id)).reason == "orig"
    await engine.dispose()


async def test_update_rule_allow_retired_applies(monkeypatch):
    factory, rule_id, engine = await _rule_db(monkeypatch)
    result = _data(
        await _mcp().call_tool(
            "update_rule",
            {"id": rule_id, "reason": f"moved from {RETIRED}", "allow_retired": True},
        )
    )
    assert result["updated"] is True
    async with factory() as session:
        assert (await session.get(Rule, rule_id)).reason == f"moved from {RETIRED}"
    await engine.dispose()


async def test_update_rule_current_address_applies_with_hint(monkeypatch):
    factory, rule_id, engine = await _rule_db(monkeypatch)
    result = _data(
        await _mcp().call_tool("update_rule", {"id": rule_id, "reason": f"host {CURRENT}"})
    )
    assert result["updated"] is True and "hint" in result
    await engine.dispose()


# --- REST + public route ---------------------------------------------------


def _app(monkeypatch):
    from src.core.app import create_app
    from src.core.config import get_settings

    monkeypatch.setenv("BRAIN_TYPE", "infra")
    monkeypatch.setenv("MCP_ACCESS_KEY", "a" * 64)
    monkeypatch.setenv("CONTRIBUTOR_KEY", "b" * 64)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("POSTGRES_HOST", "x")
    monkeypatch.setenv("POSTGRES_USER", "x")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("POSTGRES_DB", "x")
    get_settings.cache_clear()
    return create_app()


async def test_rest_proposal_hint_only(monkeypatch):
    app = _app(monkeypatch)
    _RecordingRepo.added = []

    class _RestRepo(_RecordingRepo):
        async def add(self, data):
            type(self).added.append(data)
            return type(
                "Rec",
                (),
                {
                    "id": 1,
                    "app": None,
                    "title": data["title"],
                    "content": data["content"],
                    "tags": [],
                    "severity": "INFO",
                    "source": None,
                    "status": data["status"],
                    "authority": data["authority"],
                    "applicability": {},
                    "conflict_kind": data.get("conflict_kind"),
                },
            )()

    monkeypatch.setattr(infra_module, "get_session_factory", lambda: lambda: _Session())
    monkeypatch.setattr(proposals_module, "LessonRepository", _RestRepo)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/proposals/lessons",
            headers={"x-brain-key": "b" * 64},
            json={"title": "T", "content": f"host {RETIRED}"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["lesson"]["status"] == "proposed"
    assert "conflict_kind" not in _RestRepo.added[0]
    assert body["retired_address"] == [{"match": RETIRED, "role": "prod-vps"}]


@pytest.fixture
async def env_client(monkeypatch):
    from src.core.db import get_engine

    app = _app(monkeypatch)
    async with get_engine().begin() as conn:
        await conn.run_sync(Environment.__table__.create)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c


async def _seed_env():
    from datetime import date

    from src.core.db import get_session_factory

    async with get_session_factory()() as session:
        session.add(
            Environment(
                role="prod-vps",
                description="secret description",
                provider="ovh",
                location="Vint Hill",
                addresses={"ipv4": CURRENT, "tailnet_node": "ovh-vps1"},
                current_since=date(2026, 10, 23),
                previous=[{"addresses": {"ipv4": RETIRED}, "until": "2026-10-23"}],
                updated_by="t",
            )
        )
        await session.commit()
        return await EnvironmentRepository(session).list_all()


async def test_addresses_route_unauthenticated(env_client):
    rows = await _seed_env()
    resp = await env_client.get("/api/environments/addresses")
    assert resp.status_code == 200
    body = resp.json()
    assert body == payload_from_environments(rows)
    assert body["roles"]
    for entry in body["roles"]:
        assert set(entry) == {"role", "current", "retired"}


async def test_other_routes_still_need_key(env_client):
    assert (await env_client.get("/api/rules")).status_code == 401
