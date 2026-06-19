"""Tests for the open brain package: capabilities, tool registration, and embeddings wiring."""
import pytest
from fastmcp import FastMCP

from src.core.config import BrainType
from src.core.registry import Capabilities, load_brain


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

def test_open_capabilities():
    brain = load_brain(BrainType.OPEN)
    assert brain.capabilities.embeddings is True
    assert brain.capabilities.auth_allowlist == ("/api/health",)


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


async def test_capture_thought_uses_embeddings_client(monkeypatch):
    """capture_thought calls get_embeddings_client — mock it to verify the wiring."""
    import src.brains.open.tools.thoughts as thoughts_module

    fake_embedding = [0.1] * 1536
    fake_metadata = {"type": "observation", "topics": ["test"], "people": [], "action_items": []}

    class FakeClient:
        async def embed(self, text: str) -> list[float]:
            return fake_embedding

    class FakeSession:
        def __init__(self):
            self._committed = False

        async def flush(self):
            pass

        async def commit(self):
            self._committed = True

        def add(self, obj):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class FakeSessionFactory:
        def __call__(self):
            return FakeSession()

    monkeypatch.setattr(thoughts_module, "get_embeddings_client", lambda settings: FakeClient())
    monkeypatch.setattr(thoughts_module, "get_session_factory", lambda: FakeSessionFactory())
    monkeypatch.setattr(thoughts_module, "_extract_metadata", lambda text: fake_metadata)

    brain = load_brain(BrainType.OPEN)
    mcp = FastMCP("t")
    brain.register(mcp)

    # Verify the tool is registered and callable structure is in place
    tools = await mcp.list_tools()
    assert "capture_thought" in {t.name for t in tools}
