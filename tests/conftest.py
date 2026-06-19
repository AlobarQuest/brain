"""Global test fixtures — hermeticity for the brain test suite.

Clears all ambient brain env vars before each test and cache-clears
get_settings() so lru_cache state from one test cannot bleed into the next.
This makes the suite hermetic: local == CI regardless of the developer's
shell environment.
"""
import pytest

# All env vars consumed by src.core.config.Settings
_BRAIN_ENV_VARS = [
    "BRAIN_TYPE",
    "MCP_ACCESS_KEY",
    "OPENROUTER_API_KEY",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "DATABASE_URL",
    "LOG_LEVEL",
    "APP_ENV",
]


@pytest.fixture(autouse=True)
def clear_brain_env(monkeypatch):
    """Scrub brain env vars and Settings cache before (and after) every test."""
    from src.core.config import get_settings

    for var in _BRAIN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
