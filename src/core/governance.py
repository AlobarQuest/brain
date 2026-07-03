"""Shared governance layer for the four brains: lifecycle columns, the
approver gate, the conflict engine, and the approve/reject/deprecate tools.

Base-agnostic on purpose — code-brain uses an isolated DeclarativeBase, so
GovernanceMixin must not reference any concrete Base.
"""
from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import CheckConstraint, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

# --- vocabulary -----------------------------------------------------------
STATUS_PROPOSED = "proposed"
STATUS_APPROVED = "approved"
STATUS_DEPRECATED = "deprecated"
STATUS_SUPERSEDED = "superseded"
VALID_STATUSES = (STATUS_PROPOSED, STATUS_APPROVED, STATUS_DEPRECATED, STATUS_SUPERSEDED)

AUTHORITY_INFORMATIONAL = "informational"
AUTHORITY_RECOMMENDED = "recommended"
AUTHORITY_REQUIRED = "required"
VALID_AUTHORITIES = (AUTHORITY_INFORMATIONAL, AUTHORITY_RECOMMENDED, AUTHORITY_REQUIRED)
AUTHORITATIVE = (AUTHORITY_RECOMMENDED, AUTHORITY_REQUIRED)
_AUTHORITY_RANK = {AUTHORITY_INFORMATIONAL: 0, AUTHORITY_RECOMMENDED: 1, AUTHORITY_REQUIRED: 2}

CONFLICT_DUPLICATE = "duplicate"
CONFLICT_OVERLAP = "overlap"

_JSON = sa.JSON().with_variant(JSONB, "postgresql")


def governance_check_constraints(tablename: str) -> tuple[CheckConstraint, ...]:
    """DB-level CHECKs for a governed table. Compose into each model's
    __table_args__ (avoids the mixin/__table_args__ override collision)."""
    statuses = ", ".join(f"'{s}'" for s in VALID_STATUSES)
    authorities = ", ".join(f"'{a}'" for a in VALID_AUTHORITIES)
    kinds = ", ".join(f"'{k}'" for k in (CONFLICT_DUPLICATE, CONFLICT_OVERLAP))
    return (
        CheckConstraint(f"status IN ({statuses})", name=f"ck_{tablename}_status"),
        CheckConstraint(f"authority IN ({authorities})", name=f"ck_{tablename}_authority"),
        CheckConstraint(
            f"conflict_kind IS NULL OR conflict_kind IN ({kinds})",
            name=f"ck_{tablename}_conflict_kind",
        ),
    )


class GovernanceMixin:
    """Governed-knowledge columns (companion §3.2). Base-agnostic."""

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("'proposed'"))
    authority: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sa.text("'informational'")
    )
    proposed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    applicability: Mapped[dict] = mapped_column(
        _JSON, nullable=False, server_default=sa.text("'{}'")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sa.text("1"))
    conflict_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict_acknowledged_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


# --- approver gate --------------------------------------------------------
def approver_ok(presented: str | None, approver_key: str) -> bool:
    return bool(presented) and hmac.compare_digest(presented, approver_key)


def presented_key() -> str | None:
    """The x-brain-key the current caller presented, from the FastMCP HTTP
    request. Returns None outside an HTTP request (→ callers default deny)."""
    try:
        from fastmcp.server.dependencies import get_http_request  # confirmed in Step 0

        req = get_http_request()
    except Exception:
        return None
    if req is None:
        return None
    return req.headers.get("x-brain-key") or req.query_params.get("key")


def require_approver() -> bool:
    from src.core.config import get_settings

    return approver_ok(presented_key(), get_settings().mcp_access_key)


# --- conflict engine ------------------------------------------------------
@dataclass(frozen=True)
class ConflictFlag:
    kind: str  # CONFLICT_DUPLICATE | CONFLICT_OVERLAP
    note: str


def normalized_check(check: dict | None) -> str | None:
    """Canonical JSON of a machine check (sorted keys). None/empty → None."""
    if not check:
        return None
    return json.dumps(check, sort_keys=True, separators=(",", ":"))


def overlap_signature(candidate: dict, key_fields: tuple[str, ...]) -> tuple | None:
    """Applicability signature, or None if any key field is missing/None
    (a partial key is too coarse to flag)."""
    if not key_fields:
        return None
    vals = [candidate.get(f) for f in key_fields]
    if any(v is None for v in vals):
        return None
    return tuple(vals)


async def find_conflicts(
    session,
    model,
    *,
    candidate_check: dict | None,
    overlap_key_fields: tuple[str, ...],
    candidate: dict,
    exclude_id=None,
) -> ConflictFlag | None:
    """Flag a candidate against APPROVED, recommended|required records of the
    same model. Layer 1 (duplicate, byte-identical normalized check) wins over
    Layer 2 (applicability overlap). Only the candidate is flagged; no target
    row is mutated. Runs pre-commit so the candidate is not its own target."""
    stmt = sa.select(model).where(
        model.status == STATUS_APPROVED,
        model.authority.in_(AUTHORITATIVE),
    )
    if exclude_id is not None:
        stmt = stmt.where(model.id != exclude_id)
    rows = (await session.execute(stmt)).scalars().all()

    cand_norm = normalized_check(candidate_check)
    cand_key = overlap_signature(candidate, overlap_key_fields)
    overlap_hit: ConflictFlag | None = None
    for r in rows:
        if cand_norm is not None and normalized_check(getattr(r, "check", None)) == cand_norm:
            return ConflictFlag(
                CONFLICT_DUPLICATE,
                f"duplicate check of approved {model.__tablename__} #{r.id}",
            )
        if cand_key is not None and overlap_hit is None:
            r_key = tuple(getattr(r, f, None) for f in overlap_key_fields)
            if r_key == cand_key:
                overlap_hit = ConflictFlag(
                    CONFLICT_OVERLAP,
                    f"overlaps approved {model.__tablename__} #{r.id}"
                    f" on {list(overlap_key_fields)}",
                )
    return overlap_hit
