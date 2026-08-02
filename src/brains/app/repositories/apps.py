import re
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.app.models import (
    LANDING_INERT,
    LANDING_REDEPLOYS,
    LANDING_UNKNOWN,
    App,
)


def normalize_host(value: str | None) -> str | None:
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
    coolify_app_uuid: str | None = None,
    fqdn: str | None = None,
) -> dict | None:
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


def canonical_repo_slug(value: str | None) -> str | None:
    """Reduce a stored or queried repository reference to a comparable
    "owner/repo", lowercased. None/unparseable -> None.

    Matching on the raw column would make an app INVISIBLE to the fold whenever
    its github_repo was stored in a different shape — `https://github.com/O/r`,
    `O/r.git`, a stray space — and an app that is invisible to the fold cannot
    make the answer `unknown`. It just silently is not counted, which turns a
    repository that redeploys into one that reads `inert`. So both sides are
    canonicalized, and update_app canonicalizes on the way in.

    >>> canonical_repo_slug("https://github.com/AlobarQuest/Brain.git")
    'alobarquest/brain'
    >>> canonical_repo_slug("git@github.com:AlobarQuest/brain.git")
    'alobarquest/brain'
    """
    if not value:
        return None
    s = value.strip().rstrip("/")
    if not s:
        return None
    scp = re.match(r"^[\w.+-]+@[\w.-]+:(.+)$", s)
    if scp:
        s = scp.group(1)
    else:
        url = re.match(r"^[a-zA-Z][\w+.-]*://(?:[^@/]+@)?[^/]+/(.+)$", s)
        if url:
            s = url.group(1)
    s = s.strip("/")
    if s.endswith(".git"):
        s = s[: -len(".git")]
    parts = [p for p in s.split("/") if p]
    if len(parts) < 2:
        return None
    # Both segments must look like a GitHub owner/repo name. Without this a
    # string whose scheme we failed to strip still yields two segments —
    # "https://github.com/" splits to ["https:", "github.com"] and would
    # canonicalize to the nonsense slug "https:/github.com", which then passes
    # validation and matches nothing.
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", p) for p in parts[:2]):
        return None
    return f"{parts[0]}/{parts[1]}".lower()


def aggregate_landing(github_repo: str, rows: list[dict]) -> dict:
    """Fold every REGISTERED app fed by one repository into a single answer.

    `rows` is the apps whose canonical github_repo matches, each
    {slug, default_branch_landing, determined_at, evidence}.

    One repository can feed several running apps — AlobarQuest/brain deploys
    app-brain, infra-brain, open-brain and code-brain from a single ci.yml — so
    the question "does merging here change something already running?" is a fold
    over all of them, not a per-app lookup. Asking one brain's slug alone would
    answer for a quarter of what the merge actually moves.

    Precedence is `redeploys` > `unknown` > `inert`. One assessed redeploying app
    settles the answer whatever its siblings hold, and `inert` requires that every
    matched app was assessed and came back inert. An unmatched repository is
    `unknown` — App Brain not knowing about a repository is never evidence that
    merging to it is safe.

    THE BOUND, stated exactly, because `inert` is the answer a caller reads as
    permission: this fold ranges over apps REGISTERED IN APP BRAIN. It cannot see
    an app that deploys from this repository and was never onboarded. `inert`
    therefore means "every app the estate knows of that is fed by this repository
    is inert", not "nothing anywhere redeploys". `matched_apps` is returned so a
    caller never receives `inert` without also receiving its denominator.
    """
    values = [r.get("default_branch_landing") for r in rows]
    if LANDING_REDEPLOYS in values:
        landing, reason = LANDING_REDEPLOYS, None
    elif not rows:
        landing, reason = LANDING_UNKNOWN, "no_app_record"
    elif all(v == LANDING_INERT for v in values):
        landing, reason = LANDING_INERT, None
    else:
        landing, reason = LANDING_UNKNOWN, "not_assessed"
    return {
        "github_repo": github_repo,
        "landing": landing,
        "reason": reason,
        "matched_apps": len(rows),
        "apps": sorted(rows, key=lambda r: r["slug"]),
    }


class AppRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_apps(
        self,
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict]:
        stmt = select(
            App.slug, App.name, App.status, App.onboarding_status, App.description, App.tags
        ).order_by(App.name)
        if status:
            stmt = stmt.where(App.status == status)
        if tags:
            stmt = stmt.where(App.tags.overlap(tags))

        result = await self.session.execute(stmt)
        return [row._asdict() for row in result.all()]

    async def get_app(self, slug: str) -> App | None:
        result = await self.session.execute(select(App).where(App.slug == slug))
        return result.scalar_one_or_none()

    async def resolve_environment(
        self,
        coolify_app_uuid: str | None = None,
        fqdn: str | None = None,
    ) -> dict | None:
        """Resolve a Coolify app (by stable app UUID, else FQDN) to its
        {github_repo, name, branch, url}. Returns None when nothing matches.

        A Python scan over (github_repo, environments) rather than a Postgres
        JSONB query: the FQDN fallback needs URL-host normalization that is
        fragile in SQL, the matching logic is then a single pure, unit-tested
        function, and N is tiny (~17 apps). The exact-UUID path could be a JSONB
        query, but keeping both paths in one place is simpler than splitting them.
        """
        result = await self.session.execute(select(App.github_repo, App.environments))
        rows = [
            {"github_repo": r.github_repo, "environments": r.environments} for r in result.all()
        ]
        return match_environment(rows, coolify_app_uuid=coolify_app_uuid, fqdn=fqdn)

    async def resolve_default_branch_landing(self, github_repo: str) -> dict:
        """Answer "does merging to this repository's default branch change
        anything already running?" for a GitHub "owner/repo" slug.

        Compared on the CANONICAL slug on both sides, in Python rather than SQL.
        A SQL `lower(github_repo) = :target` misses every row stored in a
        different shape (`https://github.com/O/r`, `O/r.git`), and a missed row
        is not merely absent from the answer — it cannot make the answer
        `unknown`, so a repository that redeploys reads `inert`. Same reasoning
        as resolve_environment, and N is tiny (~25 apps).
        """
        target = canonical_repo_slug(github_repo)
        if not target:
            return aggregate_landing(github_repo, [])
        result = await self.session.execute(
            select(
                App.slug,
                App.github_repo,
                App.default_branch_landing,
                App.default_branch_landing_determined_at,
                App.default_branch_landing_evidence,
            )
        )
        rows = [
            {
                "slug": r.slug,
                "default_branch_landing": r.default_branch_landing,
                "determined_at": (
                    r.default_branch_landing_determined_at.isoformat()
                    if r.default_branch_landing_determined_at
                    else None
                ),
                "evidence": r.default_branch_landing_evidence,
            }
            for r in result.all()
            if canonical_repo_slug(r.github_repo) == target
        ]
        return aggregate_landing(github_repo, rows)

    async def create_app(self, **kwargs) -> App:
        app = App(**kwargs)
        self.session.add(app)
        await self.session.flush()
        return app

    async def update_app(self, slug: str, **fields) -> App | None:
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
        error: str | None = None,
        onboarded_at: datetime | None = None,
    ) -> None:
        values: dict = {"onboarding_status": status, "last_onboarding_error": error}
        if onboarded_at:
            values["last_onboarded_at"] = onboarded_at
        await self.session.execute(update(App).where(App.slug == slug).values(**values))

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
