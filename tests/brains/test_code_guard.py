"""Retired-address guard on Code Brain writes."""

import json

import httpx
import pytest
import sqlalchemy as sa
from fastmcp import FastMCP

import src.brains.code as code_module
import src.brains.code.services.proposals as proposals_module
import src.brains.code.tools.exemplars as exemplars_tools_module
import src.brains.code.tools.lessons as lessons_tools_module
import src.brains.code.tools.roads as roads_tools_module
import src.brains.code.tools.rules as rules_tools_module
import src.core.governance as governance_module
from src.brains.code.models import Road
from src.core.address_guard import book_from_payload
from src.core.config import BrainType
from src.core.registry import load_brain
from tests.brains import test_code as base

code_db = base.code_db
_seed_road = base._seed_road

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

_GUARDED_MODULES = (
    lessons_tools_module,
    rules_tools_module,
    exemplars_tools_module,
    roads_tools_module,
    proposals_module,
)


@pytest.fixture(autouse=True)
def fixed_book(monkeypatch):
    async def fetch():
        return BOOK

    for module in _GUARDED_MODULES:
        monkeypatch.setattr(module, "fetch_address_book", fetch)


def _mcp() -> FastMCP:
    mcp = FastMCP("t")
    load_brain(BrainType.CODE).register(mcp)
    return mcp


def _data(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content


class _RecordingRepo:
    added: list[dict] = []

    def __init__(self, session):
        pass

    async def add(self, data):
        type(self).added.append(data)
        return type("Rec", (), {"id": 1, "title": data.get("title"), "status": data["status"]})()


async def _road(session, slug):
    return (await session.execute(sa.select(Road).where(Road.slug == slug))).scalar_one_or_none()


async def test_code_add_lesson_retired_held_with_approver_and_auto_approve(monkeypatch):
    _RecordingRepo.added = []
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    monkeypatch.setattr(
        lessons_tools_module, "get_session_factory", lambda: lambda: base._FakeCommitSession()
    )
    monkeypatch.setattr(lessons_tools_module, "LessonRepository", _RecordingRepo)
    result = _data(
        await _mcp().call_tool(
            "add_lesson",
            {"title": "Deploy", "content": f"ssh root@{RETIRED}", "auto_approve": True},
        )
    )
    assert result["status"] == "proposed"
    assert _RecordingRepo.added[0]["conflict_kind"] == "retired_address"
    assert result["retired_address"] == [{"match": RETIRED, "role": "prod-vps"}]
    assert "prod-vps" in json.dumps(result)


async def test_code_add_lesson_current_address_saves_with_hint(monkeypatch):
    _RecordingRepo.added = []
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    monkeypatch.setattr(
        lessons_tools_module, "get_session_factory", lambda: lambda: base._FakeCommitSession()
    )
    monkeypatch.setattr(lessons_tools_module, "LessonRepository", _RecordingRepo)
    result = _data(
        await _mcp().call_tool("add_lesson", {"title": "Deploy", "content": f"host {CURRENT}"})
    )
    assert result["status"] == "approved"
    assert "prod-vps" in result["hint"]
    assert "retired_address" not in result


async def test_code_add_rule_retired_held(code_db, monkeypatch):
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    await _seed_road(code_db, slug="road-g")
    result = _data(
        await _mcp().call_tool(
            "add_rule",
            {
                "road_slug": "road-g",
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


async def test_code_add_exemplar_retired_held(code_db, monkeypatch):
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    await _seed_road(code_db, slug="road-h")
    result = _data(
        await _mcp().call_tool(
            "add_exemplar",
            {
                "road_slug": "road-h",
                "label": "E",
                "location": "x",
                "note": f"see {RETIRED}",
                "auto_approve": True,
            },
        )
    )
    assert result["status"] == "proposed"
    assert result["retired_address"] == [{"match": RETIRED, "role": "prod-vps"}]


_ROAD = {
    "slug": "road-r",
    "name": "R",
    "category": "quality",
    "status": "unpaved",
    "summary": "a road",
}


async def test_add_road_retired_refused(code_db):
    result = _data(
        await _mcp().call_tool("add_road", {**_ROAD, "decided_approach": f"use {RETIRED}"})
    )
    assert result["error"] == "retired_address"
    assert result["matches"] == [{"match": RETIRED, "role": "prod-vps"}]
    async with code_db() as session:
        assert await _road(session, "road-r") is None


async def test_add_road_allow_retired_creates(code_db):
    result = _data(
        await _mcp().call_tool(
            "add_road",
            {**_ROAD, "decided_approach": f"use {RETIRED}", "allow_retired": True},
        )
    )
    assert result["created"] is True


async def test_update_road_retired_refused(code_db):
    await _seed_road(code_db, slug="road-u", decided_approach="orig")
    result = _data(
        await _mcp().call_tool(
            "update_road", {"slug": "road-u", "decided_approach": f"use {RETIRED}"}
        )
    )
    assert result["error"] == "retired_address"
    async with code_db() as session:
        assert (await _road(session, "road-u")).decided_approach == "orig"


async def test_update_road_allow_retired_applies(code_db):
    await _seed_road(code_db, slug="road-v", decided_approach="orig")
    result = _data(
        await _mcp().call_tool(
            "update_road",
            {"slug": "road-v", "decided_approach": f"use {RETIRED}", "allow_retired": True},
        )
    )
    assert result["updated"] is True
    async with code_db() as session:
        assert (await _road(session, "road-v")).decided_approach == f"use {RETIRED}"


async def test_update_road_current_address_applies_with_hint(code_db):
    await _seed_road(code_db, slug="road-w")
    result = _data(
        await _mcp().call_tool("update_road", {"slug": "road-w", "home": f"host {CURRENT}"})
    )
    assert result["updated"] is True and "prod-vps" in result["hint"]


async def test_code_rest_proposal_hint_only(monkeypatch):
    app = base._code_app(monkeypatch)
    _RecordingRepo.added = []

    class _RestRepo(_RecordingRepo):
        async def add(self, data):
            type(self).added.append(data)
            lesson = base._FakeLesson()
            lesson.status = data["status"]
            return lesson

    monkeypatch.setattr(proposals_module, "LessonRepository", _RestRepo)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/proposals/lessons",
            headers={"x-brain-key": "b" * 64},
            json={"title": "T", "content": f"host {RETIRED}"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert "conflict_kind" not in _RestRepo.added[0]
    assert body["retired_address"] == [{"match": RETIRED, "role": "prod-vps"}]
    assert code_module  # route lives in the package under test
