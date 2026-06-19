"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-03-18

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("package", sa.Text(), nullable=False, unique=True),
        sa.Column("canonical", sa.Text(), nullable=False),
        sa.Column("min_allowed", sa.Text(), nullable=True),
        sa.Column("blocked_above", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("confirmed_in", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("ecosystem", sa.Text(), nullable=False, server_default="python"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by", sa.Text(), nullable=False, server_default="ai-capture"),
    )

    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("rule", sa.Text(), nullable=False, unique=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_app", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("severity IN ('BLOCK', 'WARN', 'INFO')", name="rules_severity_check"),
    )

    op.create_table(
        "combos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("packages", postgresql.JSONB(), nullable=False),
        sa.Column("ecosystem", sa.Text(), nullable=False),
        sa.Column("flavor", sa.Text(), nullable=True),
        sa.Column("confirmed_in", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "lessons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("app", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, unique=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("severity", sa.Text(), nullable=False, server_default="INFO"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("source", sa.Text(), nullable=False, server_default="ai-capture"),
        sa.CheckConstraint("severity IN ('CRITICAL', 'WARN', 'INFO')", name="lessons_severity_check"),
    )


def downgrade() -> None:
    op.drop_table("lessons")
    op.drop_table("combos")
    op.drop_table("rules")
    op.drop_table("versions")
