import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.app.models import App


def normalize_host(value: Optional[str]) -> Optional[str]:
    """Reduce a URL or host string to a bare, comparable host.

    Strips scheme, path, and query; lowercases; trims a trailing dot/slash.
    >>> normalize_host("https://Booking.devonwatkins.com/")
    'booking.devonwatkins.com'
    >>> normalize_host("booking.devonwatkins.com")
    'booking.devonwatkins.com'
    """
    if not value:
        return None
    v = value.strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0].split("?", 1)[0]
    v = v.strip().rstrip(".")
    return v or None


def match_environment(
    rows: list[dict],
    coolify_app_uuid: Optional[str] = None,
    fqdn: Optional[str] = None,
) -> Optional[dict]:
    """Resolve a deployment environment to {github_repo, name, branch, url}.

    `rows` is a list of {github_repo, environments[]} records. Resolution order
    (per the REST contract): EXACT `coolify_app_uuid` across all environments
    first; FALLBACK to `fqdn` host match against each environment's `url` host.
    Returns the first match joined with its app's github_repo, or None. Never
    guesses — branch/url are returned exactly as stored (may be null).
    """
    def _joined(github_repo, env):
        return {
            "github_repo": github_repo,
            "name": env.get("name"),
            "branch": env.get("branch"),
            "url": env.get("url"),
        }

    if coolify_app_uuid:
        for row in rows:
            for env in row.get("environments") or []:
                if env.get("coolify_app_uuid") == coolify_app_uuid:
                    return _joined(row.get("github_repo"), env)

    if fqdn:
        target = normalize_host(fqdn)
        if target:
            for row in rows:
                for env in row.get("environments") or []:
                    if normalize_host(env.get("url")) == target:
                        return _joined(row.get("github_repo"), env)

    return None


class AppRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_apps(
        self,
        status: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        stmt = (
            select(App.slug, App.name, App.status, App.onboarding_status, App.description, App.tags)
            .order_by(App.name)
        )
        if status:
            stmt = stmt.where(App.status == status)
        if tags:
            stmt = stmt.where(App.tags.overlap(tags))

        result = await self.session.execute(stmt)
        return [row._asdict() for row in result.all()]

    async def get_app(self, slug: str) -> Optional[App]:
        result = await self.session.execute(
            select(App).where(App.slug == slug)
        )
        return result.scalar_one_or_none()

    async def resolve_environment(
        self,
        coolify_app_uuid: Optional[str] = None,
        fqdn: Optional[str] = None,
    ) -> Optional[dict]:
        """Resolve a Coolify app (by stable app UUID, else FQDN) to its
        {github_repo, name, branch, url}. Returns None when nothing matches.

        A Python scan over (github_repo, environments) rather than a Postgres
        JSONB query: the FQDN fallback needs URL-host normalization that is
        fragile in SQL, the matching logic is then a single pure, unit-tested
        function, and N is tiny (~17 apps). The exact-UUID path could be a JSONB
        query, but keeping both paths in one place is simpler than splitting them.
        """
        result = await self.session.execute(select(App.github_repo, App.environments))
        rows = [{"github_repo": r.github_repo, "environments": r.environments} for r in result.all()]
        return match_environment(rows, coolify_app_uuid=coolify_app_uuid, fqdn=fqdn)

    async def create_app(self, **kwargs) -> App:
        app = App(**kwargs)
        self.session.add(app)
        await self.session.flush()
        return app

    async def update_app(self, slug: str, **fields) -> Optional[App]:
        app = await self.get_app(slug)
        if not app:
            return None
        for key, value in fields.items():
            setattr(app, key, value)
        await self.session.flush()
        return app

    async def mark_onboarding_status(
        self,
        slug: str,
        status: str,
        error: Optional[str] = None,
        onboarded_at: Optional[datetime] = None,
    ) -> None:
        values: dict = {"onboarding_status": status, "last_onboarding_error": error}
        if onboarded_at:
            values["last_onboarded_at"] = onboarded_at
        await self.session.execute(
            update(App).where(App.slug == slug).values(**values)
        )

    async def fail_stale_running(self) -> int:
        """Mark any app stuck in 'running' (from an interrupted job) as 'failed'."""
        result = await self.session.execute(
            update(App)
            .where(App.onboarding_status == "running")
            .values(
                onboarding_status="failed",
                last_onboarding_error="onboarding interrupted by restart",
            )
        )
        return result.rowcount
