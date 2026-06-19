import importlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from src.core.config import BrainType

@dataclass(frozen=True)
class Capabilities:
    embeddings: bool = False
    auth_allowlist: tuple[str, ...] = ("/api/health",)

@runtime_checkable
class BrainModule(Protocol):
    capabilities: Capabilities
    def register(self, mcp) -> None: ...

def load_brain(brain_type) -> BrainModule:
    try:
        return importlib.import_module(f"src.brains.{brain_type.value}")
    except ModuleNotFoundError as e:
        raise ValueError(f"unknown brain: {brain_type.value}") from e
