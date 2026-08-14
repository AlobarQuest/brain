"""FastAPI + FastMCP host — the integration hub for the unified brain server.

Ported from ~/Projects/infra-brain/src/main.py (FastMCP-3 reference).
Key differences vs. infra-brain:
  - Registry-driven tool registration via brain.register(mcp)
  - Per-brain auth allowlist from brain.capabilities.auth_allowlist
  - brain param in create_app() for test-time injection (stub brain, no real packages)
  - make_auth_middleware abstracted into src.core.auth
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import sqlalchemy
from fastapi import FastAPI, Response
from fastmcp import FastMCP

from src.core.auth import make_auth_middleware
from src.core.config import get_settings
from src.core.db import make_engine
from src.core.mcp_alias import MCPPrefixAlias
from src.core.registry import BrainModule, load_brain

MCP_HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]


def create_app(brain: BrainModule | None = None) -> FastAPI:
    """Build and return the FastAPI application.

    Args:
        brain: Optional pre-built brain module.  When None (production),
               ``load_brain(settings.brain_type)`` is used.  Pass a stub
               in tests to avoid importing the real brain packages.
    """
    settings = get_settings()

    if brain is None:
        brain = load_brain(settings.brain_type)

    # FastMCP 3.x: do NOT pass transport kwargs to the constructor.
    mcp = FastMCP("brain")
    brain.register(mcp)

    # FastMCP 3.x: transport kwargs go on http_app(), not the constructor.
    mcp_app = mcp.http_app(path="/", json_response=True, stateless_http=True)

    # Shared app-scoped engine — one per app instance, reused across requests.
    engine = make_engine(settings.effective_database_url())

    # Propagate FastMCP's lifespan to FastAPI so the session manager starts.
    # Without this, /mcp returns 500 ("task group not initialized").
    # Engine is disposed on shutdown alongside the MCP lifespan.
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            async with mcp_app.router.lifespan_context(mcp_app):
                if hasattr(brain, "startup"):
                    await brain.startup()
                yield
        finally:
            await engine.dispose()

    app = FastAPI(title="brain", lifespan=lifespan)
    app.state.engine = engine

    # Auth: x-brain-key header or ?key= query param; allowlist paths bypass it.
    app.add_middleware(
        make_auth_middleware(
            settings.mcp_access_key,
            settings.contributor_key,
            brain.capabilities.auth_exact,
            brain.capabilities.auth_prefixes,
            settings.read_key,
            brain.capabilities.read_paths,
        )
    )

    # Mount the MCP subtree at /mcp/ (handles /mcp/... requests).
    app.mount("/mcp", mcp_app)

    # Route alias: makes /mcp (no trailing slash) behave like /mcp/.
    # CRITICAL: this is a *route*, not global middleware — adding it as
    # middleware would rewrite every path (including /api/health) and
    # break routing.  See infra-brain/src/main.py lines 79-84.
    app.add_route(
        "/mcp",
        MCPPrefixAlias(mcp_app, "/mcp"),
        methods=MCP_HTTP_METHODS,
        include_in_schema=False,
    )

    @app.get("/api/health")
    async def health() -> Response:
        """DB-aware health check, plus WHICH build is answering.

        In allowlist → no auth required.

        The revision is what makes a post-deploy check able to fail. Coolify serves the
        old container throughout a rolling swap, so a poll for `status == "ok"` alone
        returns 200 the entire time and passes whether or not the new image ever
        started. `ci.yml` polls until every brain it deployed reports the commit it just
        built. The field name matches change-manager's, so one reader works for both.

        Unset outside a built image (local runs, tests), where there is no revision.
        """
        try:
            async with engine.begin() as conn:
                await conn.execute(sqlalchemy.text("SELECT 1"))
            status, code = "ok", 200
        except Exception:
            status, code = "degraded", 503
        revision = os.environ.get("GIT_SHA", "unknown")
        return Response(
            content=json.dumps({"status": status, "revision": revision}),
            status_code=code,
            media_type="application/json",
        )

    # Optional per-brain REST routes (beyond /api/health and /mcp). These are
    # auth-protected by the middleware above unless listed in the brain's
    # auth_exact/auth_prefixes. infra-brain restores GET /api/rules here, which
    # the infraops standards audit consumes.
    if hasattr(brain, "register_routes"):
        brain.register_routes(app)

    return app
