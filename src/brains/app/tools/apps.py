from datetime import datetime, timezone

from fastmcp import FastMCP

from src.brains.app.models import LANDING_UNKNOWN, LANDING_VALUES
from src.brains.app.repositories.apps import AppRepository, canonical_repo_slug
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
        # Projected to the string 'unknown', never served as null. A falsy value
        # invites `if not landing: proceed`, which reads "nobody looked" as
        # permission — the exact inference this record exists to remove. The REST
        # route makes the same projection; the two surfaces must agree.
        "default_branch_landing": app.default_branch_landing or LANDING_UNKNOWN,
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
        if "github_repo" in fields:
            # Refuse a shape the landing fold cannot match. An app stored as
            # "https://github.com/O/r" is invisible to a query for "O/r", and an
            # invisible app cannot make the answer 'unknown' — it silently is not
            # counted, turning a repository that redeploys into one reading 'inert'.
            if canonical_repo_slug(fields["github_repo"]) is None:
                return {"error": "invalid_params: github_repo must be 'owner/repo'"}

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

        Passing landing='unknown' RETRACTS a determination, clearing all three
        columns back to null. Without it the fail-closed state would be reachable
        only until someone first wrote, so a determination later found to have
        been read against the wrong repository could be corrected only to a claim
        you cannot support. Retracting takes no evidence, because you are
        withdrawing an assertion rather than making one.

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
        retracting = landing == LANDING_UNKNOWN
        if not retracting:
            if landing not in LANDING_VALUES:
                allowed = ", ".join((*LANDING_VALUES, LANDING_UNKNOWN))
                return {"error": f"invalid_params: landing must be one of {allowed}"}
            if not evidence or not evidence.strip():
                return {"error": "invalid_params: evidence is required (what did you read?)"}

        determined_at = None if retracting else datetime.now(timezone.utc)
        async with get_session_factory()() as session:
            repo = AppRepository(session)
            app = await repo.update_app(
                slug,
                default_branch_landing=None if retracting else landing,
                default_branch_landing_determined_at=determined_at,
                default_branch_landing_evidence=None if retracting else evidence.strip(),
            )
            if not app:
                return {"error": "not_found"}
            await session.commit()

        return {
            "slug": slug,
            "default_branch_landing": LANDING_UNKNOWN if retracting else landing,
            "determined_at": determined_at.isoformat() if determined_at else None,
        }
