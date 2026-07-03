"""Tests for the app brain package: capabilities, tool registration, and embeddings wiring."""
import uuid

import pytest
import sqlalchemy as sa
from fastmcp import FastMCP
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import src.brains.app.tools.knowledge as knowledge_tools_module
import src.core.db as db_module
import src.core.governance as governance_module
from src.brains.app.models import AppKnowledge
from src.core.config import BrainType
from src.core.registry import Capabilities, load_brain

# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_app_capabilities():
    brain = load_brain(BrainType.APP)
    assert brain.capabilities.embeddings is True
    assert "/register" in brain.capabilities.auth_exact
    assert "/api/health" in brain.capabilities.auth_exact
    assert "/.well-known/" in brain.capabilities.auth_prefixes


def test_app_capabilities_is_correct_type():
    brain = load_brain(BrainType.APP)
    assert isinstance(brain.capabilities, Capabilities)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "list_apps",
    "get_app",
    "update_app",
    "search_knowledge",
    "list_knowledge",
    "capture_knowledge",
    "delete_knowledge",
    "onboard_app",
    "onboard_status",
    # governance
    "approve",
    "reject",
    "deprecate",
}


async def test_app_registers_all_tools():
    brain = load_brain(BrainType.APP)
    mcp = FastMCP("t")
    brain.register(mcp)
    tools = await mcp.list_tools()
    registered = {t.name for t in tools}
    assert EXPECTED_TOOLS == registered


async def test_app_register_is_idempotent_on_fresh_mcp():
    """Each call to register on a new mcp instance succeeds."""
    brain = load_brain(BrainType.APP)
    mcp1 = FastMCP("a")
    mcp2 = FastMCP("b")
    brain.register(mcp1)
    brain.register(mcp2)
    tools1 = {t.name for t in await mcp1.list_tools()}
    tools2 = {t.name for t in await mcp2.list_tools()}
    assert tools1 == EXPECTED_TOOLS
    assert tools2 == EXPECTED_TOOLS


# ---------------------------------------------------------------------------
# Embeddings wiring — behavior-verifying test (no real network)
# ---------------------------------------------------------------------------

def test_app_embeddings_capability_declared():
    """The app brain declares embeddings=True."""
    brain = load_brain(BrainType.APP)
    assert brain.capabilities.embeddings is True


async def test_capture_knowledge_uses_embed(monkeypatch):
    """capture_knowledge calls embed() and the vector propagates into the stored AppKnowledge chunk.

    This test fails if embed() is never called or its return value is ignored — it does NOT
    pass on a hollow registration-only stub.
    """
    import src.brains.app.tools.knowledge as knowledge_module

    fake_embedding = [0.2] * 1536
    fake_metadata_result = {"topics": ["test"], "entities": [], "tags": []}

    embed_calls: list[str] = []

    async def fake_embed(text: str) -> list[float]:
        embed_calls.append(text)
        return fake_embedding

    async def fake_extract_metadata(text: str) -> dict:
        return fake_metadata_result

    stored_chunks: list = []

    class _FakeApp:
        id = uuid.UUID("00000000-0000-0000-0000-000000000001")
        slug = "test-app"

    class _FakeAppRepo:
        def __init__(self, session):
            pass

        async def get_app(self, slug: str):
            return _FakeApp()

    class _FakeKnowledgeRepo:
        def __init__(self, session):
            pass

        async def find_duplicate(self, *args, **kwargs):
            return None

        async def create(self, **kwargs):
            # Simulate ORM: create a plain object carrying the kwargs as attributes.
            obj = type("AppKnowledge", (), {})()
            for k, v in kwargs.items():
                setattr(obj, k, v)
            obj.id = uuid.UUID("00000000-0000-0000-0000-000000000002")
            stored_chunks.append(obj)
            return obj

        async def get_by_id(self, chunk_id):
            return None

    class _FakeSession:
        async def flush(self):
            pass

        async def commit(self):
            pass

        def add(self, obj):
            pass

        async def execute(self, stmt):
            # capture_knowledge's governance conflict check (find_conflicts) queries directly
            # via session.execute, bypassing the fake repos above. No approved rows exist here.
            class _EmptyResult:
                def scalars(self):
                    return self

                def all(self):
                    return []

            return _EmptyResult()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class _FakeSessionFactory:
        def __call__(self):
            return _FakeSession()

    monkeypatch.setattr(knowledge_module, "embed", fake_embed)
    monkeypatch.setattr(knowledge_module, "extract_metadata", fake_extract_metadata)
    monkeypatch.setattr(knowledge_module, "get_session_factory", lambda: _FakeSessionFactory())
    monkeypatch.setattr(knowledge_module, "AppRepository", _FakeAppRepo)
    monkeypatch.setattr(knowledge_module, "KnowledgeRepository", _FakeKnowledgeRepo)

    brain = load_brain(BrainType.APP)
    mcp = FastMCP("t")
    brain.register(mcp)

    content = "This is a test architecture knowledge chunk."
    result = await mcp.call_tool("capture_knowledge", {
        "app_slug": "test-app",
        "knowledge_type": "architecture",
        "content": content,
    })

    # embed must have been called with the chunk content
    assert embed_calls, "embed() was never called"
    assert embed_calls[0] == content, f"embed() was called with wrong text: {embed_calls[0]!r}"

    # the vector returned by fake_embed must flow into the stored chunk
    assert len(stored_chunks) == 1, f"expected 1 AppKnowledge stored, got {len(stored_chunks)}"
    assert stored_chunks[0].embedding == fake_embedding, (
        "AppKnowledge.embedding does not match the vector returned by embed()"
    )

    assert not result.is_error


# ---------------------------------------------------------------------------
# startup() hook — reconciles stale-running onboards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_calls_fail_stale_running(monkeypatch):
    """app_brain.startup() must call AppRepository.fail_stale_running() and commit."""
    import src.brains.app.repositories.apps as apps_repo_module
    import src.core.db as db_module

    calls: list[str] = []

    class _FakeAppRepo:
        def __init__(self, session):
            pass

        async def fail_stale_running(self) -> int:
            calls.append("fail_stale_running")
            return 0

    class _FakeSession:
        async def commit(self):
            calls.append("commit")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class _FakeSessionFactory:
        def __call__(self):
            return _FakeSession()

    # startup() uses local imports; patch the modules those names resolve to.
    monkeypatch.setattr(db_module, "get_session_factory", lambda: _FakeSessionFactory())
    monkeypatch.setattr(apps_repo_module, "AppRepository", _FakeAppRepo)

    import src.brains.app as app_brain_module
    await app_brain_module.startup()

    assert "fail_stale_running" in calls, "startup() did not call fail_stale_running()"
    assert "commit" in calls, "startup() did not call session.commit()"


# ---------------------------------------------------------------------------
# Governance (WS-1.4): propose-only writes, safe-retrieval filters, conflicts
#
# Exercised against a real in-memory SQLite DB (not fakes) so find_conflicts'
# actual queries run — mirrors tests/brains/test_infra.py's async-SQLite pattern.
# AppKnowledge is UUID-PK (unlike infra/code's Integer PKs) and carries a pgvector
# `embedding` column plus a JSONB `metadata` column, both Postgres-only.
# ---------------------------------------------------------------------------

def _sqlite_ddl_table(name: str, orm_table: sa.FromClause, metadata: sa.MetaData) -> sa.Table:
    """Clone an ORM table's columns onto a throwaway MetaData for the SQLite test DDL,
    substituting Postgres-only types the SQLite DDL compiler cannot render: JSONB -> portable
    JSON, postgresql.UUID -> the dialect-generic sa.Uuid, and the pgvector `embedding` column
    (which has no portable equivalent and isn't exercised by these governance tests) -> JSON,
    which can hold a plain Python list. NOTE: swapping the type rather than dropping the column
    is required — KnowledgeRepository's SELECT * queries (find_duplicate, get_by_id) reference
    every mapped column, so a physically-missing column raises "no such column: ...embedding".
    ORM inserts/selects still compile against the real mapped table's column types — only
    CREATE TABLE needs the substitution. The id column's Postgres-only `gen_random_uuid()`
    server_default is dropped from the physical DDL (SQLite can't evaluate it); the fixture
    patches a client-side Python default onto the ORM column instead. NOTE: this copies column
    types only, not table constraints (UNIQUE, self-FK, CHECK) — the clone will accept rows the
    real Postgres schema would reject."""
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
                c.name, col_type, primary_key=c.primary_key, nullable=c.nullable,
                server_default=server_default,
            )
        )
    return sa.Table(name, metadata, *cols)


def _data(result) -> dict:
    """Unwrap a mcp.call_tool() ToolResult's structured_content, which is typed Optional
    even though every app tool returns a dict."""
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
async def app_db(monkeypatch):
    """A real async-SQLite engine wired into the app knowledge tool module (and
    governance.py's lazily-imported get_session_factory), so governance write tools and
    find_conflicts run their actual queries end to end. Builds the physical schema from a
    JSONB/UUID-substituted, embedding-dropped clone of AppKnowledge's table rather than
    Base.metadata.create_all — other brains share the same declarative Base and register
    SQLite-incompatible types (e.g. open-brain's pgvector Thought)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    ddl_metadata = sa.MetaData()
    _sqlite_ddl_table(AppKnowledge.__tablename__, AppKnowledge.__table__, ddl_metadata)
    async with engine.begin() as conn:
        await conn.run_sync(ddl_metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # AppKnowledge.id relies on Postgres' gen_random_uuid() server_default, which SQLite
    # cannot evaluate at INSERT time. Patch a client-side default for this fixture's
    # duration — monkeypatch reverts it automatically at teardown.
    monkeypatch.setattr(AppKnowledge.__table__.c.id, "default", sa.ColumnDefault(uuid.uuid4))

    monkeypatch.setattr(db_module, "get_session_factory", lambda: factory)
    monkeypatch.setattr(knowledge_tools_module, "get_session_factory", lambda: factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def fake_app_and_embeddings(monkeypatch):
    """Fake out AppRepository.get_app + embed()/extract_metadata() so capture_knowledge's
    governance behavior can be exercised without a real apps table or network calls."""

    class _FakeApp:
        id = uuid.uuid4()
        slug = "widget"

    class _FakeAppRepo:
        def __init__(self, session):
            pass

        async def get_app(self, slug: str):
            return _FakeApp()

    async def fake_embed(text: str) -> list[float]:
        return [0.1] * 1536

    async def fake_extract_metadata(text: str) -> dict:
        return {"topics": [], "entities": [], "tags": []}

    monkeypatch.setattr(knowledge_tools_module, "AppRepository", _FakeAppRepo)
    monkeypatch.setattr(knowledge_tools_module, "embed", fake_embed)
    monkeypatch.setattr(knowledge_tools_module, "extract_metadata", fake_extract_metadata)


def _app_mcp() -> FastMCP:
    brain = load_brain(BrainType.APP)
    mcp = FastMCP("t")
    brain.register(mcp)
    return mcp


async def _seed_knowledge(factory, **overrides) -> uuid.UUID:
    """Insert an AppKnowledge row directly via the ORM (bypassing capture_knowledge), for
    seeding an already-approved conflict target or a fixed-status row."""
    defaults = dict(
        app_id=uuid.uuid4(),
        app_slug="widget",
        knowledge_type="architecture",
        content="seed content",
        content_hash="seedhash-" + uuid.uuid4().hex[:8],
        metadata_={},
        source="ai-capture",
        is_active=True,
        status="approved",
        authority="informational",
        applicability={},
        version=1,
    )
    defaults.update(overrides)
    async with factory() as session:
        row = AppKnowledge(**defaults)
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row.id


async def test_capture_knowledge_is_proposed_by_default(app_db, fake_app_and_embeddings):
    mcp = _app_mcp()
    added = await mcp.call_tool(
        "capture_knowledge",
        {"app_slug": "widget", "knowledge_type": "deployment", "content": "new knowledge here"},
    )
    body = _data(added)
    assert body["duplicate"] is False
    assert body["status"] == "proposed"
    assert body["conflict"] is None
    async with app_db() as session:
        row = await session.get(AppKnowledge, uuid.UUID(body["id"]))
        assert row.status == "proposed"
        assert row.authority == "informational"
        assert row.applicability == {"app_slug": "widget", "knowledge_type": "deployment"}


async def test_search_knowledge_defaults_and_threads_governance_filters(monkeypatch):
    """search_knowledge must default to include_proposed=False/min_authority=None and thread
    whatever the caller passes into BOTH the semantic and keyword repo calls, surfacing
    status/authority in the merged output. The repository's actual status/authority SQL
    filters (search_keyword's FTS query, search_semantic's pgvector function) are Postgres-only
    and can't run under SQLite — they share the exact same _allowed_statuses/_allowed_authorities
    helpers exercised for real against SQLite by the list_knowledge tests below, so this test
    verifies the tool's wiring via a spy KnowledgeRepository instead."""

    class _NullSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    calls: list[dict] = []

    class _FakeRepo:
        def __init__(self, session):
            pass

        async def search_semantic(self, **kwargs):
            calls.append({"method": "semantic", **kwargs})
            return [
                {
                    "id": "s1", "app_slug": "widget", "knowledge_type": "architecture",
                    "content": "semantic hit", "metadata": {}, "similarity": 0.9,
                    "status": "approved", "authority": "informational",
                    "applicability": {}, "conflict": None,
                }
            ]

        async def search_keyword(self, **kwargs):
            calls.append({"method": "keyword", **kwargs})
            return []

    async def fake_embed(text: str) -> list[float]:
        return [0.1] * 1536

    monkeypatch.setattr(knowledge_tools_module, "KnowledgeRepository", _FakeRepo)
    monkeypatch.setattr(knowledge_tools_module, "embed", fake_embed)
    monkeypatch.setattr(
        knowledge_tools_module, "get_session_factory", lambda: (lambda: _NullSession())
    )

    mcp = _app_mcp()

    default = await mcp.call_tool("search_knowledge", {"query": "x"})
    assert len(calls) == 2
    for call in calls:
        assert call["include_proposed"] is False
        assert call["min_authority"] is None
    assert _data(default)["results"][0]["status"] == "approved"

    calls.clear()
    await mcp.call_tool(
        "search_knowledge", {"query": "x", "include_proposed": True, "min_authority": "required"}
    )
    assert len(calls) == 2
    for call in calls:
        assert call["include_proposed"] is True
        assert call["min_authority"] == "required"


async def test_list_knowledge_excludes_deprecated_by_default(app_db):
    await _seed_knowledge(app_db, app_slug="listy", status="deprecated")
    approved_id = await _seed_knowledge(app_db, app_slug="listy", status="approved")

    mcp = _app_mcp()
    result = await mcp.call_tool("list_knowledge", {"app_slug": "listy"})
    ids = {c["id"] for c in _data(result)["chunks"]}
    assert ids == {str(approved_id)}
    chunk = next(c for c in _data(result)["chunks"] if c["id"] == str(approved_id))
    assert chunk["status"] == "approved"


async def test_list_knowledge_include_proposed_and_min_authority(app_db):
    await _seed_knowledge(app_db, app_slug="listy2", status="proposed")
    required_id = await _seed_knowledge(
        app_db, app_slug="listy2", status="approved", authority="required"
    )
    await _seed_knowledge(
        app_db, app_slug="listy2", status="approved", authority="informational"
    )

    mcp = _app_mcp()
    default = await mcp.call_tool("list_knowledge", {"app_slug": "listy2"})
    assert len(_data(default)["chunks"]) == 2  # proposed row excluded by default

    with_proposed = await mcp.call_tool(
        "list_knowledge", {"app_slug": "listy2", "include_proposed": True}
    )
    assert len(_data(with_proposed)["chunks"]) == 3

    min_authority = await mcp.call_tool(
        "list_knowledge", {"app_slug": "listy2", "min_authority": "required"}
    )
    ids = {c["id"] for c in _data(min_authority)["chunks"]}
    assert ids == {str(required_id)}


async def test_capture_knowledge_overlap_conflict_is_advisory_and_approves_without_ack(
    app_db, fake_app_and_embeddings, monkeypatch
):
    """Seed an approved, authority='required' app_knowledge row so it's a conflict target,
    then capture a second row with the same (app_slug, knowledge_type) — expect an overlap
    flag. Overlap is advisory: approve succeeds without acknowledgement, and the target row
    (the seed) is never mutated."""
    monkeypatch.setattr(governance_module, "require_approver", lambda: True)
    seed_id = await _seed_knowledge(
        app_db,
        app_slug="widget",
        knowledge_type="deployment",
        content="original deployment notes",
        status="approved",
        authority="required",
    )

    mcp = _app_mcp()
    added = await mcp.call_tool(
        "capture_knowledge",
        {
            "app_slug": "widget",
            "knowledge_type": "deployment",
            "content": "updated deployment notes, different content",
            "proposed_by": "agent-x",
        },
    )
    body = _data(added)
    assert body["conflict"] == "overlap"
    assert body["status"] == "proposed"  # auto_approve was not requested; overlap is advisory
    new_id = body["id"]

    approved = await mcp.call_tool("approve", {"record_type": "app_knowledge", "id": new_id})
    assert _data(approved)["approved"] is True
    assert _data(approved)["status"] == "approved"

    async with app_db() as session:
        new_row = await session.get(AppKnowledge, uuid.UUID(new_id))
        assert new_row.conflict_kind == "overlap"
        assert new_row.status == "approved"
        seed_row = await session.get(AppKnowledge, seed_id)
        assert seed_row.status == "approved"  # target row is never mutated
        assert seed_row.conflict_kind is None


async def test_capture_knowledge_supersede_sets_superseded_status_and_backpointer(
    app_db, fake_app_and_embeddings
):
    """A capture_knowledge call carrying supersedes_id must move the OLD row to the
    distinct 'superseded' status (not 'deprecated') and back-point it at the new row via
    superseded_by_id — status='superseded' is spec vocabulary, separate from a plain delete."""
    old_id = await _seed_knowledge(
        app_db,
        app_slug="widget",
        knowledge_type="deployment",
        content="old deployment notes",
        status="approved",
    )

    mcp = _app_mcp()
    added = await mcp.call_tool(
        "capture_knowledge",
        {
            "app_slug": "widget",
            "knowledge_type": "deployment",
            "content": "new deployment notes, replacing the old ones",
            "supersedes_id": str(old_id),
        },
    )
    body = _data(added)
    assert body["duplicate"] is False
    new_id = uuid.UUID(body["id"])

    async with app_db() as session:
        old_row = await session.get(AppKnowledge, old_id)
        assert old_row.status == "superseded"
        assert old_row.superseded_by_id == new_id
        assert old_row.is_active is False


async def test_delete_knowledge_still_yields_deprecated_status(app_db):
    """A plain delete (no supersession) must keep going through deactivate(): status stays
    'deprecated', and superseded_by_id is never set — supersede() is a distinct code path."""
    chunk_id = await _seed_knowledge(app_db, app_slug="widget", knowledge_type="deployment")

    mcp = _app_mcp()
    result = await mcp.call_tool("delete_knowledge", {"id": str(chunk_id)})
    assert _data(result)["deleted"] is True

    async with app_db() as session:
        row = await session.get(AppKnowledge, chunk_id)
        assert row.status == "deprecated"
        assert row.superseded_by_id is None
        assert row.is_active is False


# ---------------------------------------------------------------------------
# Onboarding governance (final-review FIX 3 / FIX 4):
#
# FIX 3 — onboarded knowledge is curated content (Devon-decided): chunk writes
# in run_onboarding_job must land status='approved'/authority='informational'/
# proposed_by='onboard'/reviewed_by='onboard', not proposed (the background job
# has no HTTP request context, so proposed_defaults' auto_approve could never
# fire anyway).
#
# FIX 4 — deactivate_onboard_chunks must also set status='deprecated' (not just
# is_active=False), consistent with deactivate(); combined with FIX 3, a
# re-onboard (replace_existing=True) yields new approved+visible chunks and old
# chunks deprecated+hidden — no knowledge blackout.
# ---------------------------------------------------------------------------

async def test_deactivate_onboard_chunks_sets_status_deprecated(app_db):
    """FIX 4: deactivate_onboard_chunks must deprecate governance status, not just
    flip is_active — an old onboard chunk must stop surfacing as 'approved'."""
    from src.brains.app.repositories.knowledge import KnowledgeRepository

    onboard_id = await _seed_knowledge(
        app_db, app_slug="widget", knowledge_type="architecture",
        source="onboard", status="approved", is_active=True,
    )
    # a manual/ai-capture chunk for the same app must be left untouched
    manual_id = await _seed_knowledge(
        app_db, app_slug="widget", knowledge_type="architecture",
        source="ai-capture", status="approved", is_active=True,
        content_hash="manual-hash",
    )

    async with app_db() as session:
        repo = KnowledgeRepository(session)
        count = await repo.deactivate_onboard_chunks("widget")
        await session.commit()
    assert count == 1

    async with app_db() as session:
        onboard_row = await session.get(AppKnowledge, onboard_id)
        assert onboard_row.is_active is False
        assert onboard_row.status == "deprecated"

        manual_row = await session.get(AppKnowledge, manual_id)
        assert manual_row.is_active is True
        assert manual_row.status == "approved"  # untouched — only source='onboard' affected


async def test_deactivate_onboard_chunks_excludes_given_ids(app_db):
    """exclude_ids (e.g. newly created replacement chunks) must survive deactivation
    with their governance status intact."""
    from src.brains.app.repositories.knowledge import KnowledgeRepository

    keep_id = await _seed_knowledge(
        app_db, app_slug="widget", knowledge_type="architecture",
        source="onboard", status="approved", is_active=True,
    )

    async with app_db() as session:
        repo = KnowledgeRepository(session)
        count = await repo.deactivate_onboard_chunks("widget", exclude_ids=[keep_id])
        await session.commit()
    assert count == 0

    async with app_db() as session:
        row = await session.get(AppKnowledge, keep_id)
        assert row.is_active is True
        assert row.status == "approved"


@pytest.fixture
def onboarding_env(app_db, monkeypatch):
    """Wires run_onboarding_job's own module-level imports (get_session_factory,
    classify_chunk, embed, extract_metadata, AppRepository) to the app_db SQLite
    fixture and fast fakes, so the real onboarding write path runs end to end
    without network calls or a physical apps table."""
    import src.brains.app.services.onboarding as onboarding_module

    class _FakeAppRepo:
        def __init__(self, session):
            pass

        async def mark_onboarding_status(self, slug, status, error=None, onboarded_at=None):
            pass  # no apps table in this fixture; onboarding's own write path is under test

    async def fake_classify_chunk(content, hint):
        return "architecture"

    async def fake_embed(text):
        return [0.1] * 1536  # AppKnowledge.embedding is Vector(1536); wrong dims raise ValueError

    async def fake_extract_metadata(text):
        return {"topics": [], "entities": [], "tags": []}

    # run_onboarding_job reads get_settings().onboard_concurrency directly.
    monkeypatch.setenv("BRAIN_TYPE", "app")
    monkeypatch.setenv("MCP_ACCESS_KEY", "a" * 64)
    monkeypatch.setenv("POSTGRES_HOST", "x")
    monkeypatch.setenv("POSTGRES_USER", "x")
    monkeypatch.setenv("POSTGRES_PASSWORD", "x")
    monkeypatch.setenv("POSTGRES_DB", "x")

    monkeypatch.setattr(onboarding_module, "get_session_factory", lambda: app_db)
    monkeypatch.setattr(onboarding_module, "AppRepository", _FakeAppRepo)
    monkeypatch.setattr(onboarding_module, "classify_chunk", fake_classify_chunk)
    monkeypatch.setattr(onboarding_module, "embed", fake_embed)
    monkeypatch.setattr(onboarding_module, "extract_metadata", fake_extract_metadata)
    return onboarding_module


async def test_onboarding_chunk_lands_approved(app_db, onboarding_env):
    """FIX 3: a freshly-onboarded chunk lands status='approved', not proposed."""
    app_id = uuid.uuid4()
    result = await onboarding_env.run_onboarding_job(
        app_id=app_id,
        slug="widget",
        provided_blobs={"readme.md": "Some short onboarding content about the widget app."},
        replace_existing=False,
    )
    assert result["chunks_created"] == 1

    async with app_db() as session:
        rows = (
            await session.execute(sa.select(AppKnowledge).where(AppKnowledge.app_slug == "widget"))
        ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "approved"
    assert row.authority == "informational"
    assert row.proposed_by == "onboard"
    assert row.reviewed_by == "onboard"
    assert row.reviewed_at is not None
    assert row.source == "onboard"


async def test_reonboard_replace_existing_no_blackout(app_db, onboarding_env):
    """FIX 3 + FIX 4 together: a re-onboard (replace_existing=True) leaves the old
    onboard chunk deprecated+hidden and the new chunk approved+visible — never a
    window where neither is visible."""
    app_id = uuid.uuid4()
    first = await onboarding_env.run_onboarding_job(
        app_id=app_id,
        slug="widget",
        provided_blobs={"readme.md": "First version of the widget app documentation."},
        replace_existing=False,
    )
    assert first["chunks_created"] == 1

    second = await onboarding_env.run_onboarding_job(
        app_id=app_id,
        slug="widget",
        provided_blobs={"readme.md": "Second version of the widget app documentation, updated."},
        replace_existing=True,
    )
    assert second["chunks_created"] == 1

    async with app_db() as session:
        rows = (
            await session.execute(sa.select(AppKnowledge).where(AppKnowledge.app_slug == "widget"))
        ).scalars().all()
    assert len(rows) == 2
    by_active = {row.is_active: row for row in rows}
    old_row = by_active[False]
    new_row = by_active[True]
    assert old_row.status == "deprecated"
    assert new_row.status == "approved"
