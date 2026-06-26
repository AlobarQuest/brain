"""
Idempotent backfill for the app-brain structured deployment fields added in
migration 0003 (src/brains/app/migrations/versions/0003_add_deployment_fields.py):
  - apps.github_repo   : canonical "owner/repo" resolved from the repo's git origin
  - apps.environments  : [{name, branch, url, coolify_app_uuid}] extracted from the
                         app's existing `deployment` knowledge chunks

MUST run from a machine that has the apps' local clones checked out (it reads each
app's git origin), pointed at the app-brain database via DATABASE_URL. It is NOT a
startup seed — the prod container has no local repos, so github_repo can't be
resolved there.

Design notes
------------
* CONSERVATIVE BY DESIGN. A wrong branch is worse than a missing one — the downstream
  consumer (infraops brief generator) fails safe on null. So `extract_environments`
  only emits a branch when the prose unambiguously says "<env> deploys from <branch>
  branch". Anything it isn't sure about is left out (NULL), never guessed.
* coolify_app_uuid is always NULL here. The correct identifier is unresolved (drift
  resource suffix vs Coolify application id differ for booking), so we do not hardcode
  either — open question for a later pass.
* IDEMPOTENT. Re-running recomputes the same values and overwrites the same columns.
  It never inserts rows, so it cannot duplicate. It never overwrites an existing value
  with a null/empty result, so manually-curated data is preserved.

Usage
-----
  export DATABASE_URL="postgresql+asyncpg://appbrain:password@host:5432/appbrain"
  python scripts/backfill_app_deployment_fields.py            # apply
  python scripts/backfill_app_deployment_fields.py --dry-run  # report only, no writes
  python scripts/backfill_app_deployment_fields.py --apps booking-assistant,contacts
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested in tests/brains/test_app_deployment_fields.py)     #
# --------------------------------------------------------------------------- #

def normalize_github_repo(remote_url: str | None) -> str | None:
    """Normalize a git origin URL to canonical 'owner/repo'.

    Handles https and scp-style (git@) GitHub URLs, with or without a trailing
    '.git' or '/'. Returns None for empty input or non-GitHub remotes (the field
    is specifically `github_repo`, so we don't invent a slug for other hosts).

    >>> normalize_github_repo("https://github.com/AlobarQuest/booking-system.git")
    'AlobarQuest/booking-system'
    >>> normalize_github_repo("git@github.com:AlobarQuest/booking-system.git")
    'AlobarQuest/booking-system'
    """
    if not remote_url:
        return None
    url = remote_url.strip()
    if not url:
        return None

    # scp-style: git@github.com:owner/repo(.git)
    scp = re.match(r"^[\w.+-]+@([\w.-]+):(.+)$", url)
    if scp:
        host, path = scp.group(1), scp.group(2)
    else:
        m = re.match(r"^[a-zA-Z][\w+.-]*://(?:[^@/]+@)?([^/]+)/(.+)$", url)
        if not m:
            return None
        host, path = m.group(1), m.group(2)

    if "github.com" not in host.lower():
        return None

    path = path.strip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if not owner or not repo:
        return None
    return f"{owner}/{repo}"


_ENV_NORMALIZE = {
    "production": "prod",
    "prod": "prod",
    "preview": "preview",
    "staging": "staging",
    "stage": "staging",
    "development": "dev",
    "dev": "dev",
}

# "<env> ... deploys from [the] <branch> branch [ ... to <url>]"
# The literal word "branch" must follow the captured token — this is what makes
# the match high-confidence and keeps casual mentions from producing false positives.
_DEPLOY_RE = re.compile(
    r"\b(?P<env>production|preview|staging|stage|development|prod|dev)\b"
    r"[^.;\n]{0,60}?"
    r"deploys?\s+from\s+(?:the\s+)?`?(?P<branch>[A-Za-z0-9][A-Za-z0-9._/+-]*)`?\s+branch"
    r"(?:[^.;\n]{0,80}?\bto\s+(?P<url>https?://[^\s,;`)\]]+))?",
    re.IGNORECASE,
)


def extract_environments(chunk_contents: list[str]) -> list[dict]:
    """Extract a structured per-environment deployment list from deployment prose.

    Returns records shaped exactly like the stored/exposed contract:
        {name, branch, url, coolify_app_uuid}
    coolify_app_uuid is always None (unresolved). url is None when the sentence
    states a branch but no URL. Environments are de-duplicated by name, first match
    wins (chunks are expected newest-first; the caller controls ordering).

    Only emits an environment when the prose unambiguously ties an environment name
    to a git branch via the "<env> deploys from <branch> branch" pattern.
    """
    seen: dict[str, dict] = {}
    for content in chunk_contents:
        if not content:
            continue
        for m in _DEPLOY_RE.finditer(content):
            name = _ENV_NORMALIZE.get(m.group("env").lower())
            if not name or name in seen:
                continue
            branch = m.group("branch").strip().strip("`")
            url = m.group("url")
            if url:
                url = url.rstrip(".,;:)]")
            seen[name] = {
                "name": name,
                "branch": branch,
                "url": url or None,
                "coolify_app_uuid": None,
            }
    # Stable, deterministic order: prod, preview, staging, dev, then any others.
    order = ["prod", "preview", "staging", "dev"]
    return sorted(seen.values(), key=lambda e: (order.index(e["name"]) if e["name"] in order else len(order), e["name"]))


def git_origin_url(repo_path: str | None) -> str | None:
    """Return the git origin URL for a local repo path, or None if unavailable."""
    if not repo_path or not os.path.isdir(repo_path):
        return None
    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


# --------------------------------------------------------------------------- #
# DB runner                                                                    #
# --------------------------------------------------------------------------- #

async def _run(dry_run: bool, only: set[str] | None) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL env var is required")

    engine = create_async_engine(database_url)
    repo_hits = env_hits = 0
    rows_for_report: list[tuple[str, str | None, int]] = []

    async with engine.connect() as conn:
        apps = (await conn.execute(text(
            "SELECT slug, repo_path FROM apps WHERE status = 'active' ORDER BY slug"
        ))).all()

        for slug, repo_path in apps:
            if only and slug not in only:
                continue

            github_repo = normalize_github_repo(git_origin_url(repo_path))

            chunks = (await conn.execute(
                text(
                    "SELECT content FROM app_knowledge "
                    "WHERE app_slug = :slug AND knowledge_type = 'deployment' "
                    "AND is_active = true ORDER BY created_at DESC"
                ),
                {"slug": slug},
            )).scalars().all()
            environments = extract_environments(list(chunks))

            if github_repo:
                repo_hits += 1
            if environments:
                env_hits += 1
            rows_for_report.append((slug, github_repo, len(environments)))

            if dry_run:
                continue

            # Idempotent + non-destructive: only write a column when we computed
            # something; never clobber existing data with null/empty.
            sets, params = [], {"slug": slug}
            if github_repo:
                sets.append("github_repo = :github_repo")
                params["github_repo"] = github_repo
            if environments:
                import json
                sets.append("environments = CAST(:environments AS jsonb)")
                params["environments"] = json.dumps(environments)
            if sets:
                await conn.execute(
                    text(f"UPDATE apps SET {', '.join(sets)} WHERE slug = :slug"), params
                )

        if not dry_run:
            await conn.commit()

    await engine.dispose()

    print(f"\n{'DRY RUN — ' if dry_run else ''}backfill summary")
    print(f"  apps processed     : {len(rows_for_report)}")
    print(f"  github_repo set    : {repo_hits}")
    print(f"  environments (>=1) : {env_hits}")
    print(f"  left null (no repo): {len(rows_for_report) - repo_hits}")
    print("\n  per-app:")
    for slug, gh, n in rows_for_report:
        print(f"    {slug:<28} repo={gh or 'NULL':<32} envs={n}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill app-brain deployment fields")
    parser.add_argument("--dry-run", action="store_true", help="report only, no writes")
    parser.add_argument("--apps", help="comma-separated slugs to restrict to")
    args = parser.parse_args()
    only = {s.strip() for s in args.apps.split(",")} if args.apps else None
    asyncio.run(_run(dry_run=args.dry_run, only=only))


if __name__ == "__main__":
    main()
