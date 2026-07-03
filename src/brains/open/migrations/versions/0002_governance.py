"""governance columns + backfill (WS-1.4) — thoughts only

Sub-B (deliberately asymmetric, approved): open-brain's thoughts are
OBSERVATIONS, not authority-bearing knowledge. They are governed for
uniformity with the other three brains but with NO in-place approval gate
and NO conflict detection — every thought, past and future, lands
status='approved', authority='informational'. Promotion of a thought into a
knowledge brain (with a real proposed/approved lifecycle) is WS-6.2, out of
scope here. applicability backfills to '{}' — thoughts have no meaningful
conflict key (unlike app_knowledge's app_slug/knowledge_type).

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-03

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("thoughts",)
_STATUSES = "'proposed', 'approved', 'deprecated', 'superseded'"
_AUTHORITIES = "'informational', 'recommended', 'required'"


def upgrade() -> None:
    for t in _TABLES:
        op.add_column(t, sa.Column("status", sa.Text(), server_default="proposed", nullable=False))
        op.add_column(
            t, sa.Column("authority", sa.Text(), server_default="informational", nullable=False)
        )
        op.add_column(t, sa.Column("proposed_by", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("owner", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("reviewed_by", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True))
        op.add_column(
            t,
            sa.Column(
                "applicability",
                postgresql.JSONB(),
                server_default=sa.text("'{}'::jsonb"),
                nullable=False,
            ),
        )
        op.add_column(t, sa.Column("version", sa.Integer(), server_default="1", nullable=False))
        op.add_column(t, sa.Column("conflict_note", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("conflict_kind", sa.Text(), nullable=True))
        op.add_column(
            t, sa.Column("conflict_acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True)
        )
        # thoughts had no supersession columns before this migration (unlike app_knowledge,
        # which already carried supersedes_id from its 0001) — both pointers are new here.
        # Mirrors app_knowledge's 0004 precedent: the ORM model declares these as UUID
        # self-FKs (for join/typing purposes), but no explicit DB-level FK constraint is
        # added — consistent with how app_knowledge's superseded_by_id was added.
        op.add_column(
            t, sa.Column("supersedes_id", postgresql.UUID(as_uuid=True), nullable=True)
        )
        op.add_column(
            t, sa.Column("superseded_by_id", postgresql.UUID(as_uuid=True), nullable=True)
        )
        op.create_check_constraint(f"ck_{t}_status", t, f"status IN ({_STATUSES})")
        op.create_check_constraint(f"ck_{t}_authority", t, f"authority IN ({_AUTHORITIES})")
        op.create_check_constraint(
            f"ck_{t}_conflict_kind",
            t,
            "conflict_kind IS NULL OR conflict_kind IN ('duplicate', 'overlap')",
        )

    # --- backfill (Sub-B): ALL existing thoughts are pre-approved observations ---
    op.execute(
        "UPDATE thoughts SET status = 'approved', authority = 'informational', "
        "proposed_by = 'migration:ws-1.4', reviewed_by = 'migration:ws-1.4', reviewed_at = now()"
    )
    # applicability stays the column default ('{}'::jsonb) for every row — thoughts are
    # excluded from conflict detection and have no meaningful applicability key.


def downgrade() -> None:
    cols = (
        "status", "authority", "proposed_by", "owner", "reviewed_by", "reviewed_at",
        "applicability", "version", "conflict_note", "conflict_kind",
        "conflict_acknowledged_at", "supersedes_id", "superseded_by_id",
    )
    for t in _TABLES:
        for c in ("status", "authority", "conflict_kind"):
            op.drop_constraint(f"ck_{t}_{c}", t, type_="check")
        for c in cols:
            op.drop_column(t, c)
