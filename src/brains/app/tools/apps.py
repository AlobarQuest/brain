from fastmcp import FastMCP

from src.brains.app.repositories.apps import AppRepository
from src.brains.app.repositories.knowledge import KnowledgeRepository
from src.brains.app.services.classifier import KNOWLEDGE_TYPES
from src.core.db import get_session_factory

APP_STATUSES = ["active", "archived", "in-progress", "paused"]
UPDATABLE_FIELDS = [
    "name",
    "description",
    "tech_stack",
    "status",
    "deployment_url",
    "tags",
    "repo_path",
    "github_repo",
    "environments",
]


def serialize_app_profile(app, coverage: dict) -> dict:
    """Build the get_app response. This dict IS the public contract consumed by
    downstream agents (e.g. the infraops brief generator); keep it stable and
    additive. `github_repo` is "owner/repo" or null; `environments` is a list of
    {name, branch, url, coolify_app_uuid} records (branch/url null when unknown)."""
    return {
        "slug": app.slug,
        "name": app.name,
        "description": app.description,
        "tech_stack": app.tech_stack,
        "repo_path": app.repo_path,
        "deployment_url": app.deployment_url,
        "github_repo": app.github_repo,
        "environments": app.environments,
        "status": app.status,
        "tags": app.tags,
        "onboarding_status": app.onboarding_status,
        "last_onboarded_at": app.last_onboarded_at.isoformat() if app.last_onboarded_at else None,
        "created_at": app.created_at.isoformat() if app.created_at else None,
        "coverage": coverage,
    }


def register_app_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def list_apps(
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        """List all registered apps with optional filters by status or tags."""
        async with get_session_factory()() as session:
            repo = AppRepository(session)
            apps = await repo.list_apps(status=status, tags=tags)
        return {"apps": apps}

    @mcp.tool()
    async def get_app(slug: str) -> dict:
        """Get full profile and knowledge coverage for one app. Call before asking the user questions the brain might already answer."""
        async with get_session_factory()() as session:
            app_repo = AppRepository(session)
            app = await app_repo.get_app(slug)
            if not app:
                return {"error": "not_found"}

            knowledge_repo = KnowledgeRepository(session)
            type_counts = await knowledge_repo.count_by_type(slug)

        coverage = {t: type_counts.get(t, 0) for t in KNOWLEDGE_TYPES}

        return serialize_app_profile(app, coverage)

    @mcp.tool()
    async def update_app(
        slug: str,
        name: str | None = None,
        description: str | None = None,
        tech_stack: dict | None = None,
        status: str | None = None,
        deployment_url: str | None = None,
        tags: list[str] | None = None,
        repo_path: str | None = None,
        github_repo: str | None = None,
        environments: list[dict] | None = None,
    ) -> dict:
        """Update structured fields on an app (name, description, tech_stack, status, tags, repo_path, deployment_url, github_repo, environments).

        environments is a list of {name, branch, url, coolify_app_uuid} records describing,
        per environment, the git branch each one deploys from.
        """
        all_fields = {
            "name": name,
            "description": description,
            "tech_stack": tech_stack,
            "status": status,
            "deployment_url": deployment_url,
            "tags": tags,
            "repo_path": repo_path,
            "github_repo": github_repo,
            "environments": environments,
        }
        fields = {k: v for k, v in all_fields.items() if v is not None}
        if not fields:
            return {"error": "invalid_params: no updatable fields provided"}
        if "status" in fields and fields["status"] not in APP_STATUSES:
            return {"error": f"invalid_params: status must be one of {', '.join(APP_STATUSES)}"}

        async with get_session_factory()() as session:
            repo = AppRepository(session)
            app = await repo.update_app(slug, **fields)
            if not app:
                return {"error": "not_found"}
            await session.commit()

        return {
            "slug": app.slug,
            "name": app.name,
            "status": app.status,
            "updated": list(fields.keys()),
        }
