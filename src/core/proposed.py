"""Approver-only listing of proposed governed records (GET /api/proposed).

The auth middleware admits both the approver and contributor keys, so the route
re-checks the presented key against the approver key. ``require_approver`` reads
the FastMCP request context, which a plain REST route does not have, so the
comparison is made on the FastAPI request but through the same ``approver_ok``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from sqlalchemy import select
from starlette.responses import JSONResponse

from src.core.config import get_settings
from src.core.db import get_session_factory
from src.core.governance import STATUS_PROPOSED, approver_ok

SUMMARY_MAX = 500

# (model class, row -> the record's fields other than record_type)
ProposedSource = tuple[type, Callable[[Any], dict]]


def proposed_fields(row: Any, title: str, summary: str | None) -> dict:
    """The ProposedRecord fields common to every brain, for a brain's serializer."""
    return {
        "id": row.id if isinstance(row.id, int) else str(row.id),
        "title": title,
        "summary": (summary or "")[:SUMMARY_MAX],
        "proposed_by": row.proposed_by,
        "conflict_kind": row.conflict_kind,
        "conflict_note": row.conflict_note,
    }


def register_proposed_route(app: FastAPI, sources: dict[str, ProposedSource]) -> None:
    """Register GET /api/proposed. Records are grouped by record_type in the
    order of ``sources``, id descending within each type."""

    @app.get("/api/proposed")
    async def list_proposed(request: Request):
        presented = request.headers.get("x-brain-key") or request.query_params.get("key")
        if not approver_ok(presented, get_settings().mcp_access_key):
            return JSONResponse({"error": "not_authorized"}, status_code=403)
        records: list[dict] = []
        async with get_session_factory()() as session:
            for record_type, (model, to_record) in sources.items():
                result = await session.execute(
                    select(model).where(model.status == STATUS_PROPOSED).order_by(model.id.desc())
                )
                records.extend(
                    {"record_type": record_type, **to_record(row)} for row in result.scalars()
                )
        return {"records": records}
