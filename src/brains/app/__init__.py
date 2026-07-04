from src.brains.app.models import AppKnowledge
from src.brains.app.tools.apps import register_app_tools
from src.brains.app.tools.knowledge import register_knowledge_tools
from src.core.governance import register_governance_tools
from src.core.registry import Capabilities

capabilities = Capabilities(
    embeddings=True,
    auth_exact=("/api/health", "/register"),
    auth_prefixes=("/.well-known/",),
)


def register(mcp) -> None:
    register_app_tools(mcp)
    register_knowledge_tools(mcp)
    register_governance_tools(mcp, {"app_knowledge": AppKnowledge})


def register_routes(app) -> None:
    """REST endpoints beyond /api/health (auth-protected by the core middleware,
    i.e. require the x-brain-key header or ?key= — the consumer passes APPBRAIN_ACCESS_KEY).

    GET /api/apps/resolve lets the headless infraops drift pipeline (Node, no MCP)
    resolve a Coolify app -> {github_repo, name, branch, url} over HTTP. Mirrors the
    infra brain's GET /api/rules. Additive; the MCP get_app tool is unchanged.
    """
    from starlette.responses import JSONResponse

    @app.get("/api/apps/resolve")
    async def resolve_app(coolify_app_uuid: str | None = None, fqdn: str | None = None):
        """Resolve by stable Coolify app UUID (exact) first, then FQDN host (fallback).

        400 if neither param is given; 404 when nothing matches (the consumer treats
        404 as UNCONFIRMED). branch/url may be null — returned as-is, never guessed.
        """
        if not coolify_app_uuid and not fqdn:
            return JSONResponse(
                {"error": "at least one of coolify_app_uuid or fqdn is required"},
                status_code=400,
            )

        from src.brains.app.repositories.apps import AppRepository
        from src.core.db import get_session_factory

        async with get_session_factory()() as session:
            record = await AppRepository(session).resolve_environment(
                coolify_app_uuid=coolify_app_uuid, fqdn=fqdn
            )
        if record is None:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return record


async def startup() -> None:
    from src.brains.app.repositories.apps import AppRepository
    from src.core.db import get_session_factory

    async with get_session_factory()() as session:
        await AppRepository(session).fail_stale_running()
        await session.commit()
