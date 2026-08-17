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


async def _health(monkeypatch, git_sha: str | None):
    from src.core.app import create_app

    if git_sha is None:
        monkeypatch.delenv("GIT_SHA", raising=False)
    else:
        monkeypatch.setenv("GIT_SHA", git_sha)
    app = create_app(brain=StubBrain())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        return await client.get("/api/health")


@pytest.mark.asyncio
async def test_health_returns_200_ok(monkeypatch):
    resp = await _health(monkeypatch, None)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_reports_the_running_revision(monkeypatch):
    """ci.yml polls this field to tell the new container from the one it replaced."""
    resp = await _health(monkeypatch, "abc123")
    assert resp.json() == {"status": "ok", "revision": "abc123"}


@pytest.mark.asyncio
async def test_health_revision_is_unknown_outside_a_built_image(monkeypatch):
    resp = await _health(monkeypatch, None)
    assert resp.json()["revision"] == "unknown"


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
