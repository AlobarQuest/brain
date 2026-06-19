"""Tests for the x-brain-key auth middleware."""
import pytest
import httpx

from fastapi import FastAPI

from src.core.auth import make_auth_middleware

KEY = "a" * 64
EXACT = ("/api/health",)


def build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(make_auth_middleware(KEY, exact=EXACT))

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
async def test_exact_match_does_not_exempt_subpath():
    """EXACT semantics: /register exempt, /register/foo must return 401."""
    exact_app = FastAPI()
    exact_app.add_middleware(make_auth_middleware(KEY, exact=("/register",), prefixes=()))

    @exact_app.get("/register")
    async def reg():
        return {"ok": True}

    @exact_app.get("/register/foo")
    async def reg_foo():
        return {"ok": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=exact_app), base_url="http://testserver"
    ) as client:
        resp_exact = await client.get("/register")
        resp_sub = await client.get("/register/foo")

    assert resp_exact.status_code == 200, "/register should be exempt"
    assert resp_sub.status_code == 401, "/register/foo must NOT be exempt under exact matching"


@pytest.mark.asyncio
async def test_prefix_match_exempts_subpath():
    """PREFIX semantics: /.well-known/ in prefixes exempts /.well-known/x."""
    prefix_app = FastAPI()
    prefix_app.add_middleware(make_auth_middleware(KEY, exact=(), prefixes=("/.well-known/",)))

    @prefix_app.get("/.well-known/jwks.json")
    async def jwks():
        return {"keys": []}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=prefix_app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/.well-known/jwks.json")

    assert resp.status_code == 200, "/.well-known/jwks.json should be exempt via prefix"
