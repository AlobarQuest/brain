"""Integration test for FastAPI+FastMCP host and /api/health endpoint."""

import httpx
import pytest

from src.core.registry import Capabilities

KEY = "a" * 64


class StubBrain:
    """Minimal brain that satisfies BrainModule protocol without real packages."""

    capabilities = Capabilities()

    def register(self, mcp) -> None:  # noqa: ANN001
        pass


@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    """Set required env vars so get_settings() succeeds inside create_app()."""
    from src.core.config import get_settings

    monkeypatch.setenv("BRAIN_TYPE", "infra")
    monkeypatch.setenv("MCP_ACCESS_KEY", KEY)
    # SQLite in-memory for hermeticity (no Postgres needed)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    # Postgres fields are still required by Settings; provide stubs.
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("POSTGRES_DB", "d")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_health_returns_200_ok():
    from src.core.app import create_app

    app = create_app(brain=StubBrain())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_protected_path_requires_key():
    """Paths outside the allowlist return 401 without x-brain-key."""
    from src.core.app import create_app

    app = create_app(brain=StubBrain())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/some-protected-path")
    assert resp.status_code == 401
