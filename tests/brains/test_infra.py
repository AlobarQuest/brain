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
