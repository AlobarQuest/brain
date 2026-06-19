"""add_rule_mutation_columns

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-13

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("rules", sa.Column("retired_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("rules", sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("rules", sa.Column("updated_by", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("rules", "updated_by")
    op.drop_column("rules", "updated_at")
    op.drop_column("rules", "retired_at")
