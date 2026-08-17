from src.brains.app.models import AppKnowledge
from src.brains.app.tools.apps import register_app_tools
from src.brains.app.tools.knowledge import register_knowledge_tools
from src.core.governance import register_governance_tools
from src.core.registry import Capabilities

READ_PATHS = ("/api/apps/resolve", "/api/apps/default-branch-landing")

capabilities = Capabilities(
    embeddings=True,
    auth_exact=("/api/health",),
    auth_prefixes=(),
    read_paths=READ_PATHS,
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

    GET /api/apps/default-branch-landing answers, for a GitHub "owner/repo" slug,
    whether merging to its default branch changes anything already running. Both
    are listed in READ_PATHS, so a READ_KEY holder can call them and nothing else.
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

    @app.get("/api/apps/default-branch-landing")
    async def default_branch_landing(github_repo: str | None = None):
        """Does merging to this repository's default branch change anything
        already running?

        Keyed on the GitHub "owner/repo" slug because that is what a caller
        reasoning about a repository actually holds; /api/apps/resolve's
        coolify_app_uuid and fqdn describe the deploy target, which such a caller
        has no way to name. Matched case-insensitively.

        Returns 200 with landing = 'redeploys' | 'inert' | 'unknown', the
        determination's provenance, and the apps this repository feeds. 400 when
        github_repo is absent.

        The answer is now the REPOSITORY's recorded determination rather than a
        fold over its apps, so it also resolves a repository the factory targets
        with nothing deployed from it — `matched_apps: 0` with a real `landing`
        is that case, and it was unreachable before. Every key of the previous
        response is unchanged in name and meaning; `determined_at` and `evidence`
        are additive, and carry the provenance for a repository that has no apps
        to carry it.

        Never 404: 'unknown' is an answer and must appear on the wire as one. A
        404 would have to be mapped to unknown by every caller — the same
        inference this record exists to remove — and it is indistinguishable from
        the route being absent because the brain has not been redeployed, which
        is a failure this estate has already shipped once.
        """
        if not github_repo:
            return JSONResponse({"error": "github_repo is required"}, status_code=400)

        from src.brains.app.repositories.apps import AppRepository
        from src.core.db import get_session_factory

        async with get_session_factory()() as session:
            return await AppRepository(session).resolve_default_branch_landing(github_repo)


async def startup() -> None:
    from src.brains.app.repositories.apps import AppRepository
    from src.core.db import get_session_factory

    async with get_session_factory()() as session:
        await AppRepository(session).fail_stale_running()
        await session.commit()
