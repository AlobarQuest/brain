"""environments registry

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-07

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("addresses", postgresql.JSONB(), nullable=False),
        sa.Column("current_since", sa.Date(), nullable=False),
        sa.Column(
            "previous",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.Text(), server_default="devon", nullable=False),
    )
    op.create_index("ix_environments_role", "environments", ["role"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_environments_role", table_name="environments")
    op.drop_table("environments")
