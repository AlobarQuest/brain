"""Shared governance layer for the four brains: lifecycle columns, the
approver gate, the conflict engine, and the approve/reject/deprecate tools.

Base-agnostic on purpose — code-brain uses an isolated DeclarativeBase, so
GovernanceMixin must not reference any concrete Base.
"""

from __future__ import annotations

import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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
AUTHORITY_RANK = _AUTHORITY_RANK  # public alias — consumed by brains' repository filters

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


# --- write-path helpers + governance tools --------------------------------
APPROVER_IDENTITY = "devon"  # registry human-operator identity (WS-1.2); reviewed_by value


def proposed_defaults(*, proposed_by: str | None, applicability: dict, auto_approve: bool) -> dict:
    data: dict = {
        "status": STATUS_PROPOSED,
        "authority": AUTHORITY_INFORMATIONAL,
        "proposed_by": proposed_by or "unattributed",
        "applicability": applicability or {},
        "version": 1,
    }
    if auto_approve and require_approver():
        data["status"] = STATUS_APPROVED
        data["reviewed_by"] = APPROVER_IDENTITY
        data["reviewed_at"] = datetime.now(timezone.utc)
    return data


def finalize_governance(data: dict, flag: ConflictFlag | None) -> None:
    """Write the conflict flag onto an insert dict. A DUPLICATE flag also
    cancels any auto-approve (never approve over an unacknowledged duplicate);
    an OVERLAP flag is advisory and leaves auto-approve intact."""
    if flag is None:
        return
    data["conflict_note"] = flag.note
    data["conflict_kind"] = flag.kind
    if flag.kind == CONFLICT_DUPLICATE and data.get("status") == STATUS_APPROVED:
        data["status"] = STATUS_PROPOSED
        data.pop("reviewed_by", None)
        data.pop("reviewed_at", None)


def _coerce_record_id(model: type, id: int | str | uuid.UUID) -> int | str | uuid.UUID:
    """Coerce an incoming MCP-call id to the governed model's actual primary-key type.

    Governed models use either an Integer PK (infra/code brains) or a UUID PK (app-brain's
    AppKnowledge). A UUID PK's id always arrives over MCP as a str, but SQLAlchemy's Uuid
    bind processor requires a real uuid.UUID instance (it calls .hex on the value) — passing
    the raw string through to session.get() raises AttributeError. An Integer PK's id can also
    arrive as a str: the tool signature is `int | str` (to accommodate UUID-PK brains), and
    under Pydantic v2 a JSON string like "5" is validated as str, not coerced to int — passing
    it through unchanged to session.get() against an Integer column is rejected by asyncpg
    (the prod driver). Native int ids pass through unchanged in both cases. An id that is
    already a uuid.UUID (never arrives that way over MCP, but a caller within this process
    may pass one) also passes through unchanged against a UUID-PK model. A caller should
    catch ValueError (raised by both uuid.UUID() and int() on a malformed string) and surface
    the shared invalid_id error."""
    pk_type = sa.inspect(model).primary_key[0].type
    if isinstance(pk_type, sa.Uuid) and not isinstance(id, uuid.UUID):
        return uuid.UUID(str(id))
    if isinstance(pk_type, Integer) and isinstance(id, str):
        return int(id)
    return id


async def _get_governed_record(
    session, records: dict[str, type], record_type: str, id: int | str
) -> tuple[Any, dict | None]:
    """Resolve record_type → model, load the row. Returns (rec, None) on
    success or (None, error_dict) — shared by approve/reject/deprecate so each
    tool only has to handle its own status transition."""
    model = records.get(record_type)
    if model is None:
        return None, {"error": "unknown_record_type", "allowed": list(records)}
    try:
        pk_id = _coerce_record_id(model, id)
    except ValueError:
        return None, {"error": "invalid_id", "record_type": record_type, "id": id}
    rec = await session.get(model, pk_id)
    if rec is None:
        return None, {"error": "not_found", "record_type": record_type, "id": id}
    return rec, None


async def _approve_record(
    records: dict[str, type], record_type: str, id: int | str, acknowledge_conflict: bool
) -> dict:
    """Implementation behind the `approve` MCP tool (kept module-level, out of
    the nested-closure body, so register_governance_tools stays simple)."""
    from src.core.db import get_session_factory

    if not require_approver():
        return {"error": "not_authorized", "hint": "approval requires the approver key"}
    async with get_session_factory()() as session:
        rec, err = await _get_governed_record(session, records, record_type, id)
        if err:
            return err
        if (
            rec.conflict_kind == CONFLICT_DUPLICATE
            and rec.conflict_acknowledged_at is None
            and not acknowledge_conflict
        ):
            return {"error": "conflict_unacknowledged", "conflict_note": rec.conflict_note}
        rec.status = STATUS_APPROVED
        rec.reviewed_by = APPROVER_IDENTITY
        rec.reviewed_at = datetime.now(timezone.utc)
        if rec.conflict_kind == CONFLICT_DUPLICATE and acknowledge_conflict:
            rec.conflict_acknowledged_at = datetime.now(timezone.utc)
        await session.commit()
        return {"approved": True, "record_type": record_type, "id": id, "status": rec.status}


async def _reject_record(
    records: dict[str, type], record_type: str, id: int | str, reason: str
) -> dict:
    """Implementation behind the `reject` MCP tool."""
    from src.core.db import get_session_factory

    if not require_approver():
        return {"error": "not_authorized"}
    async with get_session_factory()() as session:
        rec, err = await _get_governed_record(session, records, record_type, id)
        if err:
            return err
        rec.status = STATUS_DEPRECATED
        rec.reviewed_by = APPROVER_IDENTITY
        rec.reviewed_at = datetime.now(timezone.utc)
        note = f"REJECTED: {reason}"
        rec.conflict_note = f"{rec.conflict_note} | {note}" if rec.conflict_note else note
        await session.commit()
        return {"rejected": True, "record_type": record_type, "id": id}


async def _deprecate_record(records: dict[str, type], record_type: str, id: int | str) -> dict:
    """Implementation behind the `deprecate` MCP tool."""
    from src.core.db import get_session_factory

    if not require_approver():
        return {"error": "not_authorized"}
    async with get_session_factory()() as session:
        rec, err = await _get_governed_record(session, records, record_type, id)
        if err:
            return err
        rec.status = STATUS_DEPRECATED
        await session.commit()
        return {"deprecated": True, "record_type": record_type, "id": id}


def register_governance_tools(mcp, records: dict[str, type]) -> None:
    """Register approve/reject/deprecate for a brain's governed record types.
    `records` maps a record_type name → its GovernanceMixin model class."""

    @mcp.tool()
    async def approve(record_type: str, id: int | str, acknowledge_conflict: bool = False) -> dict:
        """Approve a proposed record (proposed→approved). APPROVER KEY ONLY.
        A duplicate-conflict flag must be acknowledged (acknowledge_conflict=True);
        an overlap flag is advisory and never blocks. id is int for Integer-PK record
        types (infra/code brains) or a UUID string for UUID-PK types (e.g. app_knowledge)."""
        return await _approve_record(records, record_type, id, acknowledge_conflict)

    @mcp.tool()
    async def reject(record_type: str, id: int | str, reason: str) -> dict:
        """Reject a proposed record (→ deprecated). APPROVER KEY ONLY. The reason
        is appended to conflict_note (prefixed REJECTED:), preserving any flag."""
        return await _reject_record(records, record_type, id, reason)

    @mcp.tool()
    async def deprecate(record_type: str, id: int | str) -> dict:
        """Deprecate an approved record (approved→deprecated). APPROVER KEY ONLY."""
        return await _deprecate_record(records, record_type, id)
