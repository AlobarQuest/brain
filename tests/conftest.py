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
    from src.core.db import get_engine, get_session_factory

    for var in _BRAIN_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    # Also disable .env file loading: pydantic-settings reads the file as a
    # source independent of os.environ, so delenv alone leaves a local .env
    # (e.g. left over from a compose session) able to leak into Settings().
    from pydantic_settings import SettingsConfigDict

    from src.core.config import Settings

    monkeypatch.setattr(Settings, "model_config", SettingsConfigDict(env_file=None, extra="ignore"))
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    yield
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
