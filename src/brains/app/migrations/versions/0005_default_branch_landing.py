"""add default_branch_landing (+ its provenance) to apps

Answers, per app: "does landing a commit on this app's default branch change
something that is already running?"

  - apps.default_branch_landing                       TEXT NULL   'redeploys' | 'inert'
  - apps.default_branch_landing_determined_at         TIMESTAMPTZ NULL
  - apps.default_branch_landing_evidence              TEXT NULL

NULL means "nobody has assessed this app" and is the state every existing row
starts in — it is never "inert". A consumer must be able to tell an assessed
"does not redeploy" from an unanswered question, so there is no permissive
default and no backfill here: the values are written afterwards, per app, by
scripts/backfill_default_branch_landing.py through the record_default_branch_landing
tool, each with the evidence it was determined from.

The second CHECK makes the fact unassertable without its provenance.

Additive, online-safe, reversible; touches no existing data. Deliberately NOT a
key inside apps.environments[], which sync_deployment_from_coolify.py overwrites
wholesale as Coolify-authoritative.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-02

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of src.brains.app.models.LANDING_VALUES. Migrations must not
# import the model — a later vocabulary change must not retroactively rewrite
# what this migration asserted. tests/brains/test_default_branch_landing.py
# pins the two in sync.
_LANDING_VALUES = ("redeploys", "inert")
_IN = ", ".join(f"'{v}'" for v in _LANDING_VALUES)


def upgrade() -> None:
    op.execute("ALTER TABLE apps ADD COLUMN IF NOT EXISTS default_branch_landing text")
    op.execute(
        "ALTER TABLE apps ADD COLUMN IF NOT EXISTS default_branch_landing_determined_at timestamptz"
    )
    op.execute("ALTER TABLE apps ADD COLUMN IF NOT EXISTS default_branch_landing_evidence text")
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


def downgrade() -> None:
    op.execute(
        "ALTER TABLE apps DROP CONSTRAINT IF EXISTS ck_apps_default_branch_landing_provenance"
    )
    op.execute("ALTER TABLE apps DROP CONSTRAINT IF EXISTS ck_apps_default_branch_landing")
    op.execute("ALTER TABLE apps DROP COLUMN IF EXISTS default_branch_landing_evidence")
    op.execute("ALTER TABLE apps DROP COLUMN IF EXISTS default_branch_landing_determined_at")
    op.execute("ALTER TABLE apps DROP COLUMN IF EXISTS default_branch_landing")
