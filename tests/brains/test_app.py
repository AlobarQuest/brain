"""Tests for the app brain package: capabilities, tool registration, and embeddings wiring."""
import uuid

import pytest
from fastmcp import FastMCP

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
    import src.core.db as db_module
    import src.brains.app.repositories.apps as apps_repo_module

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
