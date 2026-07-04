import pytest

from src.core.registry import Capabilities, load_brain


def test_capabilities_defaults():
    c = Capabilities()
    assert c.embeddings is False
    assert c.auth_exact == ("/api/health",)
    assert c.auth_prefixes == ()


def test_load_brain_unknown_raises():
    class Fake:  # mimic a BrainType with an unmapped value
        value = "does_not_exist"

    with pytest.raises(ValueError):
        load_brain(Fake())
