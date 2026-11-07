"""Retired-address guard on App Brain writes: capture_knowledge, update_app, onboard_app."""

import json
import uuid

import pytest
from fastmcp import FastMCP

import src.brains.app.tools.apps as apps_tools_module
import src.brains.app.tools.knowledge as knowledge_tools_module
import src.core.governance as governance_module
from src.brains.app.models import AppKnowledge
from src.core.address_guard import book_from_payload
from src.core.config import BrainType
from src.core.registry import load_brain
from tests.brains import test_app as base

app_db = base.app_db
fake_app_and_embeddings = base.fake_app_and_embeddings

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


def _patch_book(monkeypatch, book):
    async def fetch():
        return book

    monkeypatch.setattr(knowledge_tools_module, "fetch_address_book", fetch)
    monkeypatch.setattr(apps_tools_module, "fetch_address_book", fetch)


@pytest.fixture(autouse=True)
def fixed_book(monkeypatch):
    _patch_book(monkeypatch, BOOK)


def _mcp() -> FastMCP:
    mcp = FastMCP("t")
    load_brain(BrainType.APP).register(mcp)
    return mcp


def _as_approver(monkeypatch):
    monkeypatch.setattr(knowledge_tools_module, "require_approver", lambda: True)
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)


async def _capture(content: str, **extra) -> dict:
    result = await _mcp().call_tool(
        "capture_knowledge",
        {"app_slug": "widget", "knowledge_type": "deployment", "content": content, **extra},
    )
    return base._data(result)


# --- capture_knowledge ------------------------------------------------------


@pytest.mark.parametrize("auto_approve", [None, True])
async def test_capture_retired_held_with_approver_and_auto_approve(
    app_db, fake_app_and_embeddings, monkeypatch, auto_approve
):
    _as_approver(monkeypatch)
    extra = {} if auto_approve is None else {"auto_approve": auto_approve}
    body = await _capture(f"The app runs on {RETIRED}.", **extra)

    assert body["status"] == "proposed"
    assert body["retired_address"] == [{"match": RETIRED, "role": "prod-vps"}]
    assert "prod-vps" in body["note"] and RETIRED in body["note"]
    assert "held as proposed" in body["note"]
    async with app_db() as session:
        row = await session.get(AppKnowledge, uuid.UUID(body["id"]))
        assert row.status == "proposed"
        assert row.conflict_kind == "retired_address"


async def test_capture_retired_supersede_held_leaves_old_chunk_live(
    app_db, fake_app_and_embeddings, monkeypatch
):
    _as_approver(monkeypatch)
    old_id = await base._seed_knowledge(
        app_db, app_slug="widget", knowledge_type="deployment", content="old guidance"
    )
    body = await _capture(f"Now on {RETIRED}.", supersedes_id=str(old_id))

    assert body["status"] == "proposed"
    assert "deprecates the old chunk on approval" in body["note"]
    async with app_db() as session:
        old = await session.get(AppKnowledge, old_id)
        assert old.status == "approved"
        assert old.is_active is True
        assert old.superseded_by_id is None
        new = await session.get(AppKnowledge, uuid.UUID(body["id"]))
        assert new.supersedes_id == old_id


async def test_capture_current_address_hint(app_db, fake_app_and_embeddings, monkeypatch):
    _as_approver(monkeypatch)
    body = await _capture(f"The app runs on {CURRENT}.")

    assert body["status"] == "approved"
    assert "prod-vps" in body["hint"]
    assert "retired_address" not in body


async def test_capture_infra_unreachable_saves_normally(
    app_db, fake_app_and_embeddings, monkeypatch
):
    _as_approver(monkeypatch)
    _patch_book(monkeypatch, None)
    body = await _capture(f"The app runs on {RETIRED}.")

    assert body["status"] == "approved"
    assert "retired_address" not in body and "hint" not in body


# --- update_app / onboard_app (no database) --------------------------------


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def commit(self):
        pass


class _Repo:
    written: dict = {}

    def __init__(self, session):
        pass

    async def update_app(self, slug, **fields):
        _Repo.written = {"slug": slug, **fields}
        return type("A", (), {"slug": slug, "name": "n", "status": "active"})()

    async def get_app(self, slug):
        return None

    async def create_app(self, **fields):
        return type("A", (), {"id": uuid.uuid4()})()


def _wire(monkeypatch):
    _Repo.written = {}
    for module in (apps_tools_module, knowledge_tools_module):
        monkeypatch.setattr(module, "require_approver", lambda: True)
        monkeypatch.setattr(module, "get_session_factory", lambda: lambda: _Session())
        monkeypatch.setattr(module, "AppRepository", _Repo)


async def _call(name: str, args: dict) -> dict:
    result = await _mcp().call_tool(name, args)
    return json.loads(result.content[0].text)


async def test_update_app_retired_in_env_url_refused(monkeypatch):
    _wire(monkeypatch)
    out = await _call(
        "update_app",
        {"slug": "x", "environments": [{"name": "prod", "url": f"http://{RETIRED}:8000"}]},
    )
    assert out["error"] == "retired_address"
    assert out["matches"] == [{"match": RETIRED, "role": "prod-vps"}]
    assert _Repo.written == {}


async def test_update_app_retired_in_description_refused(monkeypatch):
    _wire(monkeypatch)
    out = await _call("update_app", {"slug": "x", "description": f"lives on {RETIRED}"})
    assert out["error"] == "retired_address"
    assert _Repo.written == {}


async def test_update_app_allow_retired_applies(monkeypatch):
    _wire(monkeypatch)
    out = await _call(
        "update_app",
        {"slug": "x", "description": f"was on {RETIRED}", "allow_retired": True},
    )
    assert out["updated"] == ["description"]
    assert "allow_retired" not in _Repo.written
    assert _Repo.written["description"] == f"was on {RETIRED}"


async def test_update_app_current_address_applies_with_hint(monkeypatch):
    _wire(monkeypatch)
    out = await _call("update_app", {"slug": "x", "description": f"on {CURRENT}"})
    assert out["updated"] == ["description"]
    assert "prod-vps" in out["hint"]


async def test_onboard_app_retired_hint_only(monkeypatch):
    _wire(monkeypatch)
    scheduled: list = []

    async def fake_job(**kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(knowledge_tools_module, "run_onboarding_job", fake_job)
    out = await _call(
        "onboard_app",
        {"slug": "x", "name": "X", "readme": f"History: moved off {RETIRED} in 2026."},
    )
    assert out["onboarding_status"] == "running"
    assert out["retired_address"] == [{"match": RETIRED, "role": "prod-vps"}]
    assert "prod-vps" in out["note"] and "held" not in out["note"]
    for task in list(knowledge_tools_module._background_tasks):
        await task
    assert len(scheduled) == 1
