"""initial_schema — roads, rules, lessons, exemplars

Revision ID: 0001
Revises:
Create Date: 2026-06-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CATEGORIES = (
    "'application', 'data', 'api', 'frontend', 'delivery-ops', 'quality', 'security', 'ai'"
)
_STATUSES = "'paved', 'partial', 'unpaved', 'paving'"


def upgrade() -> None:
    op.create_table(
        "roads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("decided_approach", sa.Text(), nullable=True),
        sa.Column("home", sa.Text(), nullable=True),
        sa.Column("owner_standard", sa.Text(), nullable=True),
        sa.Column("adr_ref", sa.Text(), nullable=True),
        sa.Column("last_validated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("validation_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(f"category IN ({_CATEGORIES})", name="roads_category_check"),
        sa.CheckConstraint(f"status IN ({_STATUSES})", name="roads_status_check"),
    )

    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("road_slug", sa.Text(), sa.ForeignKey("roads.slug"), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False, unique=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("check", postgresql.JSONB(), nullable=True),
        sa.Column("good_example", sa.Text(), nullable=True),
        sa.Column("bad_example", sa.Text(), nullable=True),
        sa.Column("retired_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("severity IN ('BLOCK', 'WARN', 'INFO')", name="rules_severity_check"),
    )

    op.create_table(
        "lessons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("road_slug", sa.Text(), sa.ForeignKey("roads.slug"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("source_app", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "exemplars",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("road_slug", sa.Text(), sa.ForeignKey("roads.slug"), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("exemplars")
    op.drop_table("lessons")
    op.drop_table("rules")
    op.drop_table("roads")
