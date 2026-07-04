"""add structured deployment fields (github_repo, environments) to apps

Additive, online-safe, reversible. Adds:
  - apps.github_repo  TEXT NULL              canonical "owner/repo"
  - apps.environments JSONB NOT NULL '[]'    [{name, branch, url, coolify_app_uuid}]

Both columns are new and default-populated, so the migration does not touch or
destroy any existing data. downgrade() drops both columns cleanly.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-26

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE apps ADD COLUMN IF NOT EXISTS github_repo text")
    op.execute(
        "ALTER TABLE apps ADD COLUMN IF NOT EXISTS environments jsonb NOT NULL DEFAULT '[]'::jsonb"
    )
    # environments must always be a JSON array (the per-environment list).
    op.execute(
        "ALTER TABLE apps ADD CONSTRAINT chk_apps_environments_array "
        "CHECK (jsonb_typeof(environments) = 'array')"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE apps DROP CONSTRAINT IF EXISTS chk_apps_environments_array")
    op.execute("ALTER TABLE apps DROP COLUMN IF EXISTS environments")
    op.execute("ALTER TABLE apps DROP COLUMN IF EXISTS github_repo")
