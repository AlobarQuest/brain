"""governance columns + backfill (WS-1.4)

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

_TABLES = ("rules", "lessons", "combos")
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
        op.add_column(t, sa.Column("supersedes_id", sa.Integer(), nullable=True))
        op.add_column(t, sa.Column("superseded_by_id", sa.Integer(), nullable=True))
        op.create_check_constraint(f"ck_{t}_status", t, f"status IN ({_STATUSES})")
        op.create_check_constraint(f"ck_{t}_authority", t, f"authority IN ({_AUTHORITIES})")
        op.create_check_constraint(
            f"ck_{t}_conflict_kind",
            t,
            "conflict_kind IS NULL OR conflict_kind IN ('duplicate', 'overlap')",
        )

    # --- backfill status: retired -> deprecated, else approved (existing = pre-approved) ---
    op.execute("UPDATE rules SET status = 'deprecated' WHERE retired_at IS NOT NULL")
    op.execute("UPDATE rules SET status = 'approved' WHERE retired_at IS NULL")
    op.execute("UPDATE lessons SET status = 'approved'")
    op.execute("UPDATE combos SET status = 'approved'")
    # reviewed_* provenance for approved rows
    for t in _TABLES:
        op.execute(f"UPDATE {t} SET proposed_by = 'migration:ws-1.4'")
        op.execute(
            f"UPDATE {t} SET reviewed_by = 'migration:ws-1.4', reviewed_at = now() "
            "WHERE status = 'approved'"
        )

    # --- backfill authority: enumerated scanner/engine-enforced security rules -> required ---
    op.execute(
        """
        UPDATE rules SET authority = 'required'
        WHERE category = 'security'
          AND rule IN ('bws.no-token-in-tracked-files', 'bws.no-token-in-git-history',
                       'bws.bootstrap-token-not-inline', 'cred.exposure-rotate')
        """
    )

    # --- backfill applicability from existing fields (drives conflict keys + filtering) ---
    op.execute(
        "UPDATE rules SET applicability = "
        "jsonb_build_object('category', category, 'source_app', source_app)"
    )
    op.execute("UPDATE lessons SET applicability = jsonb_build_object('app', app)")
    op.execute(
        "UPDATE combos SET applicability = "
        "jsonb_build_object('name', name, 'ecosystem', ecosystem)"
    )


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
