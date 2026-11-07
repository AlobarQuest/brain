"""retired_address conflict kind

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-07

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = ("rules", "lessons", "combos")
_WIDE = "conflict_kind IS NULL OR conflict_kind IN ('duplicate', 'overlap', 'retired_address')"
_NARROW = "conflict_kind IS NULL OR conflict_kind IN ('duplicate', 'overlap')"


def upgrade() -> None:
    for t in _TABLES:
        op.drop_constraint(f"ck_{t}_conflict_kind", t, type_="check")
        op.create_check_constraint(f"ck_{t}_conflict_kind", t, _WIDE)


def downgrade() -> None:
    # Rows stay proposed and keep their conflict_note; only the kind is cleared.
    for t in _TABLES:
        op.execute(f"UPDATE {t} SET conflict_kind = NULL WHERE conflict_kind = 'retired_address'")
        op.drop_constraint(f"ck_{t}_conflict_kind", t, type_="check")
        op.create_check_constraint(f"ck_{t}_conflict_kind", t, _NARROW)
