"""add_check_to_rules

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("rules", sa.Column("check", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("rules", "check")
