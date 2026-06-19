from typing import Optional

from fastmcp import FastMCP

from src.core.db import get_session_factory
from src.brains.app.repositories.apps import AppRepository
from src.brains.app.repositories.knowledge import KnowledgeRepository
from src.brains.app.services.classifier import KNOWLEDGE_TYPES

APP_STATUSES = ["active", "archived", "in-progress", "paused"]
UPDATABLE_FIELDS = ["name", "description", "tech_stack", "status", "deployment_url", "tags", "repo_path"]


def register_app_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def list_apps(
        status: Optional[str] = None,
        tags: Optional[list[str]] = None,
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

        return {
            "slug": app.slug,
            "name": app.name,
            "description": app.description,
            "tech_stack": app.tech_stack,
            "repo_path": app.repo_path,
            "deployment_url": app.deployment_url,
            "status": app.status,
            "tags": app.tags,
            "onboarding_status": app.onboarding_status,
            "last_onboarded_at": app.last_onboarded_at.isoformat() if app.last_onboarded_at else None,
            "created_at": app.created_at.isoformat() if app.created_at else None,
            "coverage": coverage,
        }

    @mcp.tool()
    async def update_app(
        slug: str,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tech_stack: Optional[dict] = None,
        status: Optional[str] = None,
        deployment_url: Optional[str] = None,
        tags: Optional[list[str]] = None,
        repo_path: Optional[str] = None,
    ) -> dict:
        """Update structured fields on an app (name, description, tech_stack, status, tags, repo_path, deployment_url)."""
        all_fields = {
            "name": name, "description": description, "tech_stack": tech_stack,
            "status": status, "deployment_url": deployment_url, "tags": tags, "repo_path": repo_path,
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

        return {"slug": app.slug, "name": app.name, "status": app.status, "updated": list(fields.keys())}
