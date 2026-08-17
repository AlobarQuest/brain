"""drop the dead apps.default_branch_landing* columns (WS-P2.30)

0005 put the default-branch landing fact on the app; 0006 created `repositories`
and moved it; the cut-over made the repository its only reader and writer. These
columns have been read by nothing since. Leaving them would let a writer believe
they had recorded something no read path consults — two owners of one fact is
the duplication the repository entity exists to remove.

Landed separately from the cut-over so that, while the cut-over was being proven
in production, reverting it needed nothing but the previous image: the old code's
columns were still there, still holding correct values.

THE DOWNGRADE REPOPULATES. Re-adding three empty columns would let the pre-0006
code answer `unknown` for every repository in the estate — a silent, total
fail-closed regression at exactly the moment someone is recovering from
something else. So it copies each repository's determination back onto every app
it feeds. Apps that share a repository get its (aggregated) evidence rather than
the per-app prose 0005 held; the values, which are what a caller acts on, are
exact.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-02

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies, as in 0005 and 0006 — a migration must not import the model.
# tests/brains/test_repositories.py pins these against 0006's.
_LANDING_VALUES = ("redeploys", "inert")
_IN = ", ".join(f"'{v}'" for v in _LANDING_VALUES)
_STRIP_SCHEME = (r"^[A-Za-z][A-Za-z0-9+.-]*://(?:[^@/]+@)?[^/]+/", "")
_STRIP_SCP = (r"^[^/@]+@[^:/]+:", "")
_STRIP_GIT_SUFFIX = (r"(\.git)?/*$", "")
_OWNER_REPO = r"^([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)(?:/|$)"

# QUALIFIED, unlike 0006's: the downgrade joins apps to repositories and BOTH
# carry a `github_repo`, so a bare reference is an ambiguous-column error rather
# than a wrong answer. Caught by running the downgrade, not by reading it.
_BARE = (
    "regexp_replace(regexp_replace(regexp_replace("
    f"btrim(a.github_repo), '{_STRIP_SCHEME[0]}', ''), '{_STRIP_SCP[0]}', ''), "
    f"'{_STRIP_GIT_SUFFIX[0]}', '')"
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE apps DROP CONSTRAINT IF EXISTS ck_apps_default_branch_landing_provenance"
    )
    op.execute("ALTER TABLE apps DROP CONSTRAINT IF EXISTS ck_apps_default_branch_landing")
    op.execute("ALTER TABLE apps DROP COLUMN IF EXISTS default_branch_landing_evidence")
    op.execute("ALTER TABLE apps DROP COLUMN IF EXISTS default_branch_landing_determined_at")
    op.execute("ALTER TABLE apps DROP COLUMN IF EXISTS default_branch_landing")


def downgrade() -> None:
    op.execute("ALTER TABLE apps ADD COLUMN IF NOT EXISTS default_branch_landing text")
    op.execute(
        "ALTER TABLE apps ADD COLUMN IF NOT EXISTS default_branch_landing_determined_at timestamptz"
    )
    op.execute("ALTER TABLE apps ADD COLUMN IF NOT EXISTS default_branch_landing_evidence text")
    # Populate BEFORE the constraints: a row carrying a value without its
    # provenance would fail the provenance CHECK on the way in.
    op.execute(
        f"""
        UPDATE apps a
        SET default_branch_landing = r.default_branch_landing,
            default_branch_landing_determined_at = r.default_branch_landing_determined_at,
            default_branch_landing_evidence = r.default_branch_landing_evidence
        FROM repositories r
        WHERE a.github_repo IS NOT NULL
          AND r.default_branch_landing IS NOT NULL
          AND r.canonical_slug = (
              SELECT lower(m[1] || '/' || m[2])
              FROM regexp_match({_BARE}, '{_OWNER_REPO}') AS m
          )
        """
    )
    op.execute(
        "ALTER TABLE apps ADD CONSTRAINT ck_apps_default_branch_landing "
        f"CHECK (default_branch_landing IS NULL OR default_branch_landing IN ({_IN}))"
    )
    op.execute(
        "ALTER TABLE apps ADD CONSTRAINT ck_apps_default_branch_landing_provenance "
        "CHECK (default_branch_landing IS NULL OR ("
        "default_branch_landing_determined_at IS NOT NULL "
        "AND btrim(default_branch_landing_evidence) <> ''))"
    )
