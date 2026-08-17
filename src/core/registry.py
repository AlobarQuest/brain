import importlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Capabilities:
    embeddings: bool = False
    auth_exact: tuple[str, ...] = ("/api/health",)
    auth_prefixes: tuple[str, ...] = ()
    # Paths a READ_KEY holder may GET. Not an auth bypass — these still require a
    # key; they are the only paths the read-only key reaches. Empty by default,
    # so a brain grants a read surface only by naming it.
    read_paths: tuple[str, ...] = ()


@runtime_checkable
class BrainModule(Protocol):
    capabilities: Capabilities

    def register(self, mcp) -> None: ...


def load_brain(brain_type) -> BrainModule:
    try:
        return importlib.import_module(f"src.brains.{brain_type.value}")
    except ModuleNotFoundError as e:
        if e.name != f"src.brains.{brain_type.value}":
            raise  # a real brain exists but its own import failed — surface it
        raise ValueError(f"unknown brain: {brain_type.value}") from e
