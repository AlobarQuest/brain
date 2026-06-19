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
