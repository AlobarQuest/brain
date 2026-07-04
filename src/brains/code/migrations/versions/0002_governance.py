"""governance columns + backfill (WS-1.4) — rules, lessons, exemplars only

Road is EXCLUDED from governance: it is the paved-road organizational catalog
(the code-brain analogue of app-brain's `apps` table), and already owns a
domain `status` column (paving state) that collides with the governance
lifecycle `status`. See docs/superpowers/specs/2026-07-03-ws14-brain-governance-design.md §3.

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

_TABLES = ("rules", "lessons", "exemplars")
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

    # --- backfill status: rules retired -> deprecated, else approved; lessons/exemplars
    # have no retired_at concept, so all rows -> approved (existing = pre-approved) ---
    op.execute("UPDATE rules SET status = 'deprecated' WHERE retired_at IS NOT NULL")
    op.execute("UPDATE rules SET status = 'approved' WHERE retired_at IS NULL")
    op.execute("UPDATE lessons SET status = 'approved'")
    op.execute("UPDATE exemplars SET status = 'approved'")
    # reviewed_* provenance for approved rows. authority stays the column default
    # ('informational') for every row — no enumerated scanner/engine-enforced set exists for
    # code-brain at migration time (see design spec §6.2's honest-scope note).
    for t in _TABLES:
        op.execute(f"UPDATE {t} SET proposed_by = 'migration:ws-1.4'")
        op.execute(
            f"UPDATE {t} SET reviewed_by = 'migration:ws-1.4', reviewed_at = now() "
            "WHERE status = 'approved'"
        )

    # --- backfill applicability from existing fields (drives conflict keys + filtering) ---
    op.execute(
        "UPDATE rules SET applicability = "
        "jsonb_build_object('road_slug', road_slug, 'category', category)"
    )
    op.execute("UPDATE lessons SET applicability = jsonb_build_object('road_slug', road_slug)")
    op.execute("UPDATE exemplars SET applicability = jsonb_build_object('road_slug', road_slug)")


def downgrade() -> None:
    cols = (
        "status",
        "authority",
        "proposed_by",
        "owner",
        "reviewed_by",
        "reviewed_at",
        "applicability",
        "version",
        "conflict_note",
        "conflict_kind",
        "conflict_acknowledged_at",
        "supersedes_id",
        "superseded_by_id",
    )
    for t in _TABLES:
        for c in ("status", "authority", "conflict_kind"):
            op.drop_constraint(f"ck_{t}_{c}", t, type_="check")
        for c in cols:
            op.drop_column(t, c)
