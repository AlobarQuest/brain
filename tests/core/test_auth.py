"""Tests for the x-brain-key auth middleware."""
import pytest
import httpx

from fastapi import FastAPI

from src.core.auth import make_auth_middleware

KEY = "a" * 64
ALLOWLIST = ("/api/health",)


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(make_auth_middleware(KEY, ALLOWLIST))

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/mcp")
    async def mcp():
        return {"data": "secret"}

    return app


@pytest.fixture()
def app():
    return build_app()


@pytest.mark.asyncio
async def test_allowlisted_path_passes_without_key(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_protected_path_without_key_returns_401(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/mcp")
    assert resp.status_code == 401
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_protected_path_with_correct_key_in_query_returns_200(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get(f"/mcp?key={KEY}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_protected_path_with_correct_key_in_header_returns_200(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/mcp", headers={"x-brain-key": KEY})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_wrong_key_returns_401(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/mcp", headers={"x-brain-key": "b" * 64})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_allowlist_is_prefix_match():
    """Paths starting with /api/health (e.g. /api/health/check) also pass through."""
    extended_app = build_app()

    @extended_app.get("/api/health/check")
    async def health_check():
        return {"status": "ok"}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=extended_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/health/check")
    assert resp.status_code == 200
