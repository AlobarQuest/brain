"""governance columns + backfill (WS-1.4) — app_knowledge only

App is EXCLUDED from governance: it is the application registry (the
app-brain analogue of infra's Version / code's Road), not knowledge content.
app_knowledge already has supersedes_id (UUID self-FK) from 0001 — reused
here, not re-added.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-03

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("app_knowledge",)
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
        # supersedes_id already exists (0001) — only the reverse pointer is new here.
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

    # --- backfill status: inactive -> deprecated, else approved (existing = pre-approved) ---
    op.execute("UPDATE app_knowledge SET status = 'deprecated' WHERE is_active = false")
    op.execute("UPDATE app_knowledge SET status = 'approved' WHERE is_active = true")
    # reviewed_* provenance for approved rows. authority stays the column default
    # ('informational') for every row — app-brain has no enumerated scanner/engine-enforced
    # required set (unlike infra's BWS rules).
    op.execute("UPDATE app_knowledge SET proposed_by = 'migration:ws-1.4'")
    op.execute(
        "UPDATE app_knowledge SET reviewed_by = 'migration:ws-1.4', reviewed_at = now() "
        "WHERE status = 'approved'"
    )

    # --- backfill applicability from existing fields (drives conflict keys + filtering) ---
    op.execute(
        "UPDATE app_knowledge SET applicability = "
        "jsonb_build_object('app_slug', app_slug, 'knowledge_type', knowledge_type)"
    )


def downgrade() -> None:
    cols = (
        "status", "authority", "proposed_by", "owner", "reviewed_by", "reviewed_at",
        "applicability", "version", "conflict_note", "conflict_kind",
        "conflict_acknowledged_at", "superseded_by_id",
    )
    for t in _TABLES:
        for c in ("status", "authority", "conflict_kind"):
            op.drop_constraint(f"ck_{t}_{c}", t, type_="check")
        for c in cols:
            op.drop_column(t, c)
