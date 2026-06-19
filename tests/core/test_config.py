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
