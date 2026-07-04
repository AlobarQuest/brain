"""Tests for the open brain package: capabilities, tool registration, and embeddings wiring."""

import uuid

import pytest
import sqlalchemy as sa
from fastmcp import FastMCP
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.brains.open.models import Thought
from src.core.config import BrainType
from src.core.governance import GovernanceMixin
from src.core.registry import Capabilities, load_brain

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_open_capabilities():
    brain = load_brain(BrainType.OPEN)
    assert brain.capabilities.embeddings is True
    assert brain.capabilities.auth_exact == ("/api/health",)
    assert brain.capabilities.auth_prefixes == ()


def test_open_capabilities_is_correct_type():
    brain = load_brain(BrainType.OPEN)
    assert isinstance(brain.capabilities, Capabilities)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "capture_thought",
    "search_thoughts",
    "list_thoughts",
    "thought_stats",
}


async def test_open_registers_all_tools():
    brain = load_brain(BrainType.OPEN)
    mcp = FastMCP("t")
    brain.register(mcp)
    tools = await mcp.list_tools()
    registered = {t.name for t in tools}
    assert EXPECTED_TOOLS == registered


async def test_open_register_is_idempotent_on_fresh_mcp():
    """Each call to register on a new mcp instance succeeds."""
    brain = load_brain(BrainType.OPEN)
    mcp1 = FastMCP("a")
    mcp2 = FastMCP("b")
    brain.register(mcp1)
    brain.register(mcp2)
    tools1 = {t.name for t in await mcp1.list_tools()}
    tools2 = {t.name for t in await mcp2.list_tools()}
    assert tools1 == EXPECTED_TOOLS
    assert tools2 == EXPECTED_TOOLS


# ---------------------------------------------------------------------------
# Embeddings client is routed through core.embeddings (no real network)
# ---------------------------------------------------------------------------


def test_open_embeddings_capability_declared():
    """The open brain declares embeddings=True."""
    brain = load_brain(BrainType.OPEN)
    assert brain.capabilities.embeddings is True


def test_open_metadata_normalizer_enforces_shape_enum_and_dates():
    from src.brains.open.tools.thoughts import DEFAULT_METADATA, _normalize_metadata

    assert _normalize_metadata({"type": "invented"}) == DEFAULT_METADATA
    assert (
        _normalize_metadata(
            {
                "type": "task",
                "people": [],
                "action_items": ["ship"],
                "dates_mentioned": ["not-a-date"],
                "topics": ["delivery"],
            }
        )
        == DEFAULT_METADATA
    )
    assert (
        _normalize_metadata(
            {
                "type": "task",
                "people": ["Devon"],
                "action_items": ["ship"],
                "dates_mentioned": ["2026-07-04"],
                "topics": ["delivery"],
            }
        )["type"]
        == "task"
    )


async def test_capture_thought_uses_embeddings_client(monkeypatch):
    """capture_thought actually calls get_embeddings_client.embed and the returned vector flows into the persisted Thought."""
    import src.brains.open.tools.thoughts as thoughts_module

    fake_embedding = [0.1] * 1536
    fake_metadata = {"type": "observation", "topics": ["test"], "people": [], "action_items": []}

    class FakeClient:
        def __init__(self):
            self.called_with: str | None = None

        async def embed(self, text: str) -> list[float]:
            self.called_with = text
            return fake_embedding

    fake_client = FakeClient()
    added_objects: list = []

    class FakeSession:
        async def flush(self):
            pass

        async def commit(self):
            pass

        def add(self, obj):
            added_objects.append(obj)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class FakeSessionFactory:
        def __call__(self):
            return FakeSession()

    async def fake_extract(text: str) -> dict:
        return fake_metadata

    # get_settings is called inside the tool body; replace it so no real Settings
    # instantiation (which needs env vars) happens during the call.
    monkeypatch.setattr(thoughts_module, "get_settings", lambda: object())
    monkeypatch.setattr(thoughts_module, "get_embeddings_client", lambda settings: fake_client)
    monkeypatch.setattr(thoughts_module, "get_session_factory", lambda: FakeSessionFactory())
    monkeypatch.setattr(thoughts_module, "_extract_metadata", fake_extract)

    brain = load_brain(BrainType.OPEN)
    mcp = FastMCP("t")
    brain.register(mcp)

    result = await mcp.call_tool("capture_thought", {"content": "hello world"})

    # embed was called with the tool input
    assert fake_client.called_with == "hello world", "embed() was not called with the input content"

    # the vector from client.embed actually flowed into the persisted Thought
    assert len(added_objects) == 1, f"expected 1 Thought added, got {len(added_objects)}"
    assert added_objects[0].embedding == fake_embedding, (
        "Thought.embedding does not match the vector returned by client.embed"
    )

    assert not result.is_error


# ---------------------------------------------------------------------------
# Governance (WS-1.4) — Sub-B, deliberately asymmetric (approved): open's
# thoughts are OBSERVATIONS, not authority-bearing knowledge. They are
# governed for uniformity with the other three brains, but with NO in-place
# approval gate and NO conflict detection — every thought lands pre-approved.
# Promotion of a thought into a knowledge brain is WS-6.2, out of scope here.
# ---------------------------------------------------------------------------


def test_thought_has_governance_mixin_columns():
    """The migration's columns must exist on the model (GovernanceMixin + UUID
    supersession), mirroring AppKnowledge's typing."""
    assert issubclass(Thought, GovernanceMixin)
    cols = Thought.__table__.columns
    for name in (
        "status",
        "authority",
        "proposed_by",
        "owner",
        "reviewed_by",
        "reviewed_at",
        "applicability",
        "version",
        "conflict_note",
        "conflict_kind",
        "conflict_acknowledged_at",
    ):
        assert name in cols, f"missing governance column: {name}"
    assert "supersedes_id" in cols
    assert "superseded_by_id" in cols


def test_open_registers_no_governance_tools():
    """open-brain has no in-place approve/reject/deprecate gate (Sub-B) — promotion
    to a knowledge brain is WS-6.2. EXPECTED_TOOLS above already pins the exact
    tool set (no governance tools included); this test names the omission directly."""
    assert not {"approve", "reject", "deprecate"} & EXPECTED_TOOLS


def _sqlite_ddl_table(name: str, orm_table: sa.FromClause, metadata: sa.MetaData) -> sa.Table:
    """Clone Thought's columns onto a throwaway MetaData for the SQLite test DDL,
    substituting Postgres-only types (JSONB -> JSON, postgresql.UUID -> sa.Uuid, the
    pgvector `embedding` column -> JSON). Mirrors tests/brains/test_app.py's helper —
    see that module's docstring for the full rationale. Table constraints (self-FK,
    CHECK) are intentionally not copied."""
    cols = []
    for c in orm_table.columns:
        col_type = c.type
        server_default = c.server_default
        if isinstance(col_type, (JSONB, Vector)):
            col_type = sa.JSON()
            server_default = sa.text("'{}'") if server_default is not None else None
        elif isinstance(col_type, PG_UUID):
            col_type = sa.Uuid(as_uuid=True)
            server_default = None
        cols.append(
            sa.Column(
                c.name,
                col_type,
                primary_key=c.primary_key,
                nullable=c.nullable,
                server_default=server_default,
            )
        )
    return sa.Table(name, metadata, *cols)


@pytest.fixture
async def open_db(monkeypatch):
    """A real async-SQLite engine wired into the open thought tool module (and
    src.core.db's lazily-imported get_session_factory), so capture_thought's actual
    insert runs end to end. Mirrors tests/brains/test_app.py's app_db fixture."""
    import src.brains.open.tools.thoughts as thoughts_module
    import src.core.db as db_module

    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    ddl_metadata = sa.MetaData()
    _sqlite_ddl_table(Thought.__tablename__, Thought.__table__, ddl_metadata)
    async with engine.begin() as conn:
        await conn.run_sync(ddl_metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # Thought.id relies on Postgres' gen_random_uuid() server_default, which SQLite
    # cannot evaluate at INSERT time. Patch a client-side default for this fixture's
    # duration — monkeypatch reverts it automatically at teardown.
    monkeypatch.setattr(Thought.__table__.c.id, "default", sa.ColumnDefault(uuid.uuid4))

    monkeypatch.setattr(db_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(thoughts_module, "get_session_factory", lambda: factory)
    yield factory
    await engine.dispose()


async def test_capture_thought_lands_approved_informational(open_db, monkeypatch):
    """Sub-B: capture_thought must NOT use proposed_defaults (which defaults new
    records to proposed) — every thought lands status='approved',
    authority='informational', proposed_by='mcp', with no conflict flag."""
    import src.brains.open.tools.thoughts as thoughts_module

    fake_embedding = [0.1] * 1536

    class FakeClient:
        async def embed(self, text: str) -> list[float]:
            return fake_embedding

    async def fake_extract(text: str) -> dict:
        return {"type": "observation", "topics": ["test"], "people": [], "action_items": []}

    monkeypatch.setattr(thoughts_module, "get_settings", lambda: object())
    monkeypatch.setattr(thoughts_module, "get_embeddings_client", lambda settings: FakeClient())
    monkeypatch.setattr(thoughts_module, "_extract_metadata", fake_extract)

    brain = load_brain(BrainType.OPEN)
    mcp = FastMCP("t")
    brain.register(mcp)

    result = await mcp.call_tool("capture_thought", {"content": "hello governance"})
    assert not result.is_error

    async with open_db() as session:
        rows = (await session.execute(sa.select(Thought))).scalars().all()
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "approved"
        assert row.authority == "informational"
        assert row.proposed_by == "mcp"
        assert row.conflict_kind is None
        assert row.conflict_note is None
