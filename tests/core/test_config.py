import pytest
from src.core.config import Settings, BrainType

BASE = dict(brain_type="infra", mcp_access_key="a"*64,
            postgres_host="db", postgres_user="u", postgres_password="p", postgres_db="d")

def test_brain_type_enum_and_url():
    s = Settings(**BASE)
    assert s.brain_type is BrainType.INFRA
    assert s.port == 8000
    assert s.effective_database_url() == "postgresql+asyncpg://u:p@db:5432/d"

def test_explicit_database_url_wins():
    s = Settings(**BASE, database_url="postgresql+asyncpg://x/y")
    assert s.effective_database_url() == "postgresql+asyncpg://x/y"

def test_bad_access_key_rejected():
    with pytest.raises(ValueError):
        Settings(**{**BASE, "mcp_access_key": "TOOSHORT"})

def test_unknown_brain_type_rejected():
    with pytest.raises(ValueError):
        Settings(**{**BASE, "brain_type": "bogus"})

def test_contributor_key_equal_to_mcp_access_key_rejected():
    """A contributor_key equal to mcp_access_key would silently make every
    contributor an approver — must be rejected at Settings construction."""
    key = "c" * 64
    with pytest.raises(ValueError, match="contributor_key must not equal mcp_access_key"):
        Settings(**{**BASE, "mcp_access_key": key, "contributor_key": key})

def test_contributor_key_different_from_mcp_access_key_accepted():
    s = Settings(**{**BASE, "mcp_access_key": "a" * 64, "contributor_key": "b" * 64})
    assert s.contributor_key == "b" * 64
