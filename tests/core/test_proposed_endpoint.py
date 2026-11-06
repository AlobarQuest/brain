"""GET /api/proposed: approver-only listing of proposed governed records.

The route is exercised through a stub brain with small SQLite governed models (the
real models use Postgres-only column types); a separate test proves each real
brain registers it.
"""

import importlib
from types import SimpleNamespace

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.core.governance import GovernanceMixin, governance_check_constraints
from src.core.proposed import SUMMARY_MAX, proposed_fields, register_proposed_route
from src.core.registry import Capabilities

APPROVER_KEY = "a" * 64
CONTRIBUTOR_KEY = "b" * 64
READ_KEY = "c" * 64


class _Base(DeclarativeBase):
    pass


class _Lesson(_Base, GovernanceMixin):
    __tablename__ = "lessons"
    __table_args__ = governance_check_constraints("lessons")
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(sa.Text)
    content: Mapped[str] = mapped_column(sa.Text)


class _Rule(_Base, GovernanceMixin):
    __tablename__ = "rules"
    __table_args__ = governance_check_constraints("rules")
    id: Mapped[int] = mapped_column(primary_key=True)
    rule: Mapped[str] = mapped_column(sa.Text)
    reason: Mapped[str] = mapped_column(sa.Text)


class _StubBrain:
    capabilities = Capabilities()

    def register(self, mcp) -> None:
        pass

    def register_routes(self, app) -> None:
        register_proposed_route(
            app,
            {
                "rule": (_Rule, lambda r: proposed_fields(r, r.rule, r.reason)),
                "lesson": (_Lesson, lambda r: proposed_fields(r, r.title, r.content)),
            },
        )


@pytest.fixture
async def client(monkeypatch):
    from src.core.app import create_app
    from src.core.config import get_settings
    from src.core.db import get_engine

    monkeypatch.setenv("BRAIN_TYPE", "infra")
    monkeypatch.setenv("MCP_ACCESS_KEY", APPROVER_KEY)
    monkeypatch.setenv("CONTRIBUTOR_KEY", CONTRIBUTOR_KEY)
    monkeypatch.setenv("READ_KEY", READ_KEY)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "d")
    get_settings.cache_clear()
    async with get_engine().begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    app = create_app(brain=_StubBrain())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _seed(*rows):
    from src.core.db import get_session_factory

    async with get_session_factory()() as session:
        session.add_all(rows)
        await session.commit()


async def _list(client, key=APPROVER_KEY):
    return await client.get("/api/proposed", headers={"x-brain-key": key})


async def test_lists_only_proposed(client):
    await _seed(
        _Lesson(id=1, title="p", content="c", status="proposed", proposed_by="agent-x"),
        _Lesson(id=2, title="a", content="c", status="approved"),
        _Lesson(id=3, title="d", content="c", status="deprecated"),
    )
    resp = await _list(client)
    assert resp.status_code == 200
    assert resp.json() == {
        "records": [
            {
                "record_type": "lesson",
                "id": 1,
                "title": "p",
                "summary": "c",
                "proposed_by": "agent-x",
                "conflict_kind": None,
                "conflict_note": None,
            }
        ]
    }


async def test_empty_is_200(client):
    resp = await _list(client)
    assert resp.status_code == 200
    assert resp.json() == {"records": []}


async def test_grouped_by_type_in_registration_order_id_descending(client):
    await _seed(
        _Lesson(id=1, title="l1", content="c"),
        _Lesson(id=2, title="l2", content="c"),
        _Rule(id=7, rule="r7", reason="why"),
        _Rule(id=9, rule="r9", reason="why"),
    )
    records = (await _list(client)).json()["records"]
    assert [(r["record_type"], r["id"]) for r in records] == [
        ("rule", 9),
        ("rule", 7),
        ("lesson", 2),
        ("lesson", 1),
    ]


async def test_summary_truncated(client):
    await _seed(_Lesson(id=1, title="t", content="x" * (SUMMARY_MAX + 100)))
    summary = (await _list(client)).json()["records"][0]["summary"]
    assert summary == "x" * SUMMARY_MAX


async def test_contributor_key_rejected(client):
    await _seed(_Lesson(id=1, title="t", content="c"))
    resp = await _list(client, CONTRIBUTOR_KEY)
    assert resp.status_code == 403
    assert resp.json() == {"error": "not_authorized"}


async def test_read_key_rejected(client):
    assert (await _list(client, READ_KEY)).status_code == 401


async def test_no_key_rejected(client):
    assert (await client.get("/api/proposed")).status_code == 401


async def test_approver_key_via_query_param(client):
    resp = await client.get("/api/proposed", params={"key": APPROVER_KEY})
    assert resp.status_code == 200


async def test_conflict_fields_passed_through(client):
    await _seed(
        _Lesson(id=1, title="t", content="c", conflict_kind="duplicate", conflict_note="same as #4")
    )
    rec = (await _list(client)).json()["records"][0]
    assert rec["conflict_kind"] == "duplicate"
    assert rec["conflict_note"] == "same as #4"


def test_app_title_derived():
    from src.brains.app import knowledge_title

    row = SimpleNamespace(app_slug="crm", knowledge_type="decision")
    row.content = "Use FUB polling\nmore"
    assert knowledge_title(row) == "crm · decision: Use FUB polling"
    row.content = ""
    assert knowledge_title(row) == "crm · decision"


def test_app_id_serialised_as_string():
    import uuid

    uid = uuid.uuid4()
    row = SimpleNamespace(id=uid, proposed_by=None, conflict_kind=None, conflict_note=None)
    assert proposed_fields(row, "t", "s")["id"] == str(uid)


@pytest.mark.parametrize("brain_type", ["app", "infra", "code"])
def test_each_governed_brain_registers_route(brain_type):
    from fastapi import FastAPI

    app = FastAPI()
    importlib.import_module(f"src.brains.{brain_type}").register_routes(app)
    assert "/api/proposed" in {getattr(r, "path", None) for r in app.routes}


def test_open_brain_has_no_route():
    from fastapi import FastAPI

    brain = importlib.import_module("src.brains.open")
    app = FastAPI()
    if hasattr(brain, "register_routes"):
        brain.register_routes(app)
    assert "/api/proposed" not in {getattr(r, "path", None) for r in app.routes}


def _capture_sources(brain_type, monkeypatch):
    """Run a brain's register_routes with register_proposed_route stubbed; return its sources."""
    from fastapi import FastAPI

    brain = importlib.import_module(f"src.brains.{brain_type}")
    captured: dict = {}
    monkeypatch.setattr(
        brain, "register_proposed_route", lambda app, sources: captured.update(sources)
    )
    brain.register_routes(FastAPI())
    return captured


def _capture_governed(brain_type, monkeypatch):
    from fastmcp import FastMCP

    brain = importlib.import_module(f"src.brains.{brain_type}")
    captured: dict = {}
    monkeypatch.setattr(
        brain, "register_governance_tools", lambda mcp, records: captured.update(records)
    )
    brain.register(FastMCP("t"))
    return captured


def _row_cases():
    from src.brains.app.models import AppKnowledge
    from src.brains.code.models import Exemplar as CodeExemplar
    from src.brains.code.models import Lesson as CodeLesson
    from src.brains.code.models import Rule as CodeRule
    from src.brains.infra.models import Combo, Lesson, Rule

    return [
        ("infra", "rule", Rule(id=1, rule="R", reason="why"), "R", "why"),
        ("infra", "lesson", Lesson(id=1, title="T", content="body"), "T", "body"),
        ("infra", "combo", Combo(id=1, name="N", description="desc"), "N", "desc"),
        ("code", "rule", CodeRule(id=1, rule="R", reason="why"), "R", "why"),
        ("code", "lesson", CodeLesson(id=1, title="T", content="body"), "T", "body"),
        ("code", "exemplar", CodeExemplar(id=1, label="L", note="note"), "L", "note"),
        (
            "app",
            "app_knowledge",
            AppKnowledge(id=1, app_slug="crm", knowledge_type="decision", content="C1\nmore"),
            "crm · decision: C1",
            "C1\nmore",
        ),
    ]


@pytest.mark.parametrize(
    "brain_type,record_type,row,title,summary",
    _row_cases(),
    ids=lambda v: v if isinstance(v, str) and len(v) < 14 else None,
)
def test_real_brain_serializers(monkeypatch, brain_type, record_type, row, title, summary):
    _model, to_record = _capture_sources(brain_type, monkeypatch)[record_type]
    out = to_record(row)
    assert out["id"] == 1
    assert out["title"] == title
    assert out["summary"] == summary


@pytest.mark.parametrize("brain_type", ["app", "infra", "code"])
def test_proposed_sources_match_governed_records(monkeypatch, brain_type):
    assert set(_capture_sources(brain_type, monkeypatch)) == set(
        _capture_governed(brain_type, monkeypatch)
    )
