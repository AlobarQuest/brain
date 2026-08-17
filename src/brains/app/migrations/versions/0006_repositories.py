"""create `repositories` and backfill it from `apps` (WS-P2.30)

Gives the registry a subject it did not have: a REPOSITORY, which may exist with
no application deployed from it. `intent-packages` and `project-standards` are
repositories the factory targets and nothing deploys — an app registry correctly
has no record of them, so a fail-closed consumer refuses half the factory's
targets on day one.

It also moves the default-branch-landing fact to the thing it is actually a
property of. "Does merging to this REPOSITORY's default branch change anything
already running?" is asked of a repository; `AlobarQuest/brain` feeds four
running apps and 0005 had to store four copies of one answer and fold them back
together on every read.

  repositories.canonical_slug                       TEXT UNIQUE NOT NULL
  repositories.github_repo                          TEXT NOT NULL   (as given)
  repositories.default_branch_landing               TEXT NULL   'redeploys' | 'inert'
  repositories.default_branch_landing_determined_at TIMESTAMPTZ NULL
  repositories.default_branch_landing_evidence      TEXT NULL

THIS MIGRATION IS DELIBERATELY UNREAD. Nothing in the codebase queries the table
at this revision: the route still folds the `apps` columns. Landing it alone
means a failure in the deploy that carries it has exactly one candidate cause,
and an image revert alone recovers — no schema step is needed, because nothing
reads what this adds. The cut-over is a separate change; dropping the now-dead
`apps` columns is a third.

The backfill folds the apps by the same precedence the read path uses
(`redeploys` > unassessed > `inert`) so the table is born agreeing with the
answer already being served. `unknown` stays unstorable here too: a repository
nobody assessed holds NULL.

Canonicalization is done in SQL, mirroring
src.brains.app.repositories.apps.canonical_repo_slug. Where the two could
disagree the SQL is the stricter one, so a row it cannot parse is simply absent
— which reads as `unknown`, never as `inert`. Production holds no such row: all
21 non-null values are already plain `owner/repo`.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-02

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of src.brains.app.models.LANDING_VALUES, for the same reason 0005
# froze it: a later vocabulary change must not retroactively rewrite what this
# migration asserted. tests/brains/test_repositories.py pins the two in sync.
_LANDING_VALUES = ("redeploys", "inert")
_IN = ", ".join(f"'{v}'" for v in _LANDING_VALUES)

# --- canonicalization, mirroring canonical_repo_slug ------------------------
# Applied in order to a trimmed github_repo, then matched. Each is (pattern,
# replacement); tests/brains/test_repositories.py replays them through Python's
# `re` against the same shapes canonical_repo_slug is tested on.
_STRIP_SCHEME = (r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]+@)?[^/]+/", "")
_STRIP_SCP = (r"^[^/@]+@[^:/]+:", "")
_STRIP_GIT_SUFFIX = (r"(\.git)?/*$", "")
_OWNER_REPO = r"^([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)(?:/|$)"

_BARE = (
    "regexp_replace(regexp_replace(regexp_replace("
    f"btrim(github_repo), '{_STRIP_SCHEME[0]}', ''), '{_STRIP_SCP[0]}', ''), "
    f"'{_STRIP_GIT_SUFFIX[0]}', '')"
)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE IF NOT EXISTS repositories (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical_slug text NOT NULL UNIQUE,
            github_repo text NOT NULL,
            default_branch_landing text,
            default_branch_landing_determined_at timestamptz,
            default_branch_landing_evidence text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_repositories_default_branch_landing
                CHECK (default_branch_landing IS NULL
                       OR default_branch_landing IN ({_IN})),
            CONSTRAINT ck_repositories_default_branch_landing_provenance
                CHECK (default_branch_landing IS NULL OR (
                       default_branch_landing_determined_at IS NOT NULL
                       AND btrim(default_branch_landing_evidence) <> '')),
            -- The key must be storable only in the one shape the read path
            -- looks up. A row keyed 'AlobarQuest/Brain' or a bare URL would be
            -- unreachable by a query for 'alobarquest/brain', and a repository
            -- the fold cannot see answers `unknown` while holding a real
            -- determination.
            CONSTRAINT ck_repositories_canonical_slug
                CHECK (canonical_slug = lower(canonical_slug)
                       AND canonical_slug ~ '{_OWNER_REPO}')
        )
        """
    )
    op.execute(
        f"""
        WITH bare AS (
            SELECT slug AS app_slug,
                   github_repo,
                   default_branch_landing AS landing,
                   default_branch_landing_determined_at AS determined_at,
                   default_branch_landing_evidence AS evidence,
                   {_BARE} AS stripped
            FROM apps
            WHERE github_repo IS NOT NULL AND btrim(github_repo) <> ''
        ),
        keyed AS (
            SELECT b.*, lower(m[1] || '/' || m[2]) AS canonical_slug
            FROM bare b, LATERAL regexp_match(b.stripped, '{_OWNER_REPO}') AS m
            WHERE m IS NOT NULL
        ),
        folded AS (
            SELECT canonical_slug,
                   min(github_repo) AS github_repo,
                   -- The read path's precedence, in SQL. bool_and(landing =
                   -- 'inert') would be WRONG: Postgres aggregates skip NULL
                   -- inputs, so one unassessed sibling would vanish and the
                   -- repository would read `inert` — the one answer a caller
                   -- takes as permission. coalesce makes the NULL count.
                   CASE
                       WHEN bool_or(landing = 'redeploys') THEN 'redeploys'
                       WHEN bool_and(coalesce(landing, '') = 'inert') THEN 'inert'
                       ELSE NULL
                   END AS landing,
                   max(determined_at) AS determined_at,
                   CASE
                       WHEN count(*) = 1 THEN min(evidence)
                       ELSE string_agg(app_slug || ': ' || evidence,
                                       E'\\n' ORDER BY app_slug)
                   END AS evidence,
                   count(*) AS app_count
            FROM keyed
            GROUP BY canonical_slug
        )
        INSERT INTO repositories (canonical_slug, github_repo,
                                  default_branch_landing,
                                  default_branch_landing_determined_at,
                                  default_branch_landing_evidence)
        SELECT canonical_slug,
               github_repo,
               landing,
               -- Provenance travels with the value or not at all; an
               -- unassessed repository must carry neither.
               CASE WHEN landing IS NULL THEN NULL ELSE determined_at END,
               CASE WHEN landing IS NULL THEN NULL ELSE evidence END
        FROM folded
        ON CONFLICT (canonical_slug) DO NOTHING
        """
    )


def downgrade() -> None:
    # apps still holds every value this table was built from at this revision,
    # so dropping it loses nothing.
    op.execute("DROP TABLE IF EXISTS repositories")
