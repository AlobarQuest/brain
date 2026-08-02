from datetime import datetime, timezone

from fastmcp import FastMCP

from src.brains.app.models import LANDING_VALUES
from src.brains.app.repositories.apps import AppRepository
from src.brains.app.repositories.knowledge import KnowledgeRepository
from src.brains.app.services.classifier import KNOWLEDGE_TYPES
from src.core.db import get_session_factory

APP_STATUSES = ["active", "archived", "in-progress", "paused"]


def serialize_app_profile(app, coverage: dict) -> dict:
    """Build the get_app response. This dict IS the public contract consumed by
    downstream agents (e.g. the infraops brief generator); keep it stable and
    additive. `github_repo` is "owner/repo" or null; `environments` is a list of
    {name, branch, url, coolify_app_uuid} records (branch/url null when unknown).
    `default_branch_landing` is 'redeploys' | 'inert' | null, null meaning nobody
    has assessed this app — never "does not redeploy"."""
    return {
        "slug": app.slug,
        "name": app.name,
        "description": app.description,
        "tech_stack": app.tech_stack,
        "repo_path": app.repo_path,
        "deployment_url": app.deployment_url,
        "github_repo": app.github_repo,
        "environments": app.environments,
        "default_branch_landing": app.default_branch_landing,
        "default_branch_landing_determined_at": (
            app.default_branch_landing_determined_at.isoformat()
            if app.default_branch_landing_determined_at
            else None
        ),
        "default_branch_landing_evidence": app.default_branch_landing_evidence,
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

    @mcp.tool()
    async def record_default_branch_landing(slug: str, landing: str, evidence: str) -> dict:
        """Record whether landing a commit on this app's default branch changes
        something already running.

        landing is 'redeploys' (merging advances something already serving, with
        no further human act) or 'inert' (nothing already serving changes until a
        separate act). An app nobody has assessed holds null, which reads as
        'unknown' and must never be read as 'inert'.

        evidence must name what was read to reach the answer — the workflow file
        and its trigger, the git-provider webhook, or the hosting platform's
        build configuration. Three mechanisms produce 'redeploys' independently
        and no single one of them is sufficient to rule the answer out: a
        workflow step, a repository webhook pointed at the deploy target, and a
        hosting platform's own git integration.

        Deliberately separate from update_app rather than three more of its
        optional fields: the value, its determination date and its evidence are
        one indivisible claim, and a DB CHECK refuses any row that carries the
        value without the other two. The date is stamped here, server-side, so a
        determination cannot be back-dated by its author.
        """
        if landing not in LANDING_VALUES:
            return {"error": f"invalid_params: landing must be one of {', '.join(LANDING_VALUES)}"}
        if not evidence or not evidence.strip():
            return {"error": "invalid_params: evidence is required (what did you read?)"}

        determined_at = datetime.now(timezone.utc)
        async with get_session_factory()() as session:
            repo = AppRepository(session)
            app = await repo.update_app(
                slug,
                default_branch_landing=landing,
                default_branch_landing_determined_at=determined_at,
                default_branch_landing_evidence=evidence.strip(),
            )
            if not app:
                return {"error": "not_found"}
            await session.commit()

        return {
            "slug": slug,
            "default_branch_landing": landing,
            "determined_at": determined_at.isoformat(),
        }
