from datetime import datetime

from fastmcp import FastMCP

from src.core.db import get_session_factory
from src.brains.code.repositories.roads import RoadRepository
from src.brains.code.repositories.rules import RuleRepository
from src.brains.code.repositories.lessons import LessonRepository
from src.brains.code.repositories.exemplars import ExemplarRepository
from src.brains.code.tools.serialize import road_dict, rule_dict, lesson_dict, exemplar_dict

CATEGORIES = {"application", "data", "api", "frontend", "delivery-ops", "quality", "security", "ai"}
STATUSES = {"paved", "partial", "unpaved", "paving"}


def _parse_ts(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp into a datetime for a timestamptz column.

    asyncpg rejects a str bind for a TIMESTAMP column, so last_validated_at must
    be converted before it reaches the DB. Returns None on an unparseable value.
    """
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def register_road_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def list_roads(
        category: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> dict:
        """List the paved-road catalog (the Golden Paths board, machine-readable). Filter by category (application/data/api/frontend/delivery-ops/quality/security/ai) and/or status (paved/partial/unpaved/paving)."""
        limit = max(1, min(limit, 200))
        async with get_session_factory()() as session:
            repo = RoadRepository(session)
            roads = await repo.list_all(category=category, status=status, limit=limit)
            return {"roads": [road_dict(r) for r in roads]}

    @mcp.tool()
    async def get_road(slug: str) -> dict:
        """Get one road plus its active rules, exemplars, and lessons. Call this before implementing a cross-cutting pattern to learn the decided approach."""
        async with get_session_factory()() as session:
            road = await RoadRepository(session).get_by_slug(slug)
            if road is None:
                return {"error": "not_found", "slug": slug}
            rules = await RuleRepository(session).list_all(road_slug=slug)
            exemplars = await ExemplarRepository(session).list_for_road(slug)
            lessons = await LessonRepository(session).list_for_road(slug)
            return {
                "road": road_dict(road),
                "rules": [rule_dict(r) for r in rules],
                "exemplars": [exemplar_dict(e) for e in exemplars],
                "lessons": [lesson_dict(l) for l in lessons],
            }

    @mcp.tool()
    async def add_road(
        slug: str,
        name: str,
        category: str,
        status: str,
        summary: str,
        decided_approach: str | None = None,
        home: str | None = None,
        owner_standard: str | None = None,
        adr_ref: str | None = None,
    ) -> dict:
        """Add a new road (cross-cutting concern) to the catalog. category and status must be valid enum values."""
        if category not in CATEGORIES:
            return {"error": "invalid_category", "allowed": sorted(CATEGORIES)}
        if status not in STATUSES:
            return {"error": "invalid_status", "allowed": sorted(STATUSES)}
        async with get_session_factory()() as session:
            repo = RoadRepository(session)
            if await repo.get_by_slug(slug) is not None:
                return {"error": "already_exists", "slug": slug}
            road = await repo.add({
                "slug": slug, "name": name, "category": category, "status": status,
                "summary": summary, "decided_approach": decided_approach,
                "home": home, "owner_standard": owner_standard, "adr_ref": adr_ref,
            })
            await session.commit()
            return {"created": True, "slug": road.slug}

    @mcp.tool()
    async def update_road(
        slug: str,
        status: str | None = None,
        decided_approach: str | None = None,
        home: str | None = None,
        owner_standard: str | None = None,
        adr_ref: str | None = None,
        last_validated_at: str | None = None,
        validation_note: str | None = None,
        name: str | None = None,
        summary: str | None = None,
        category: str | None = None,
    ) -> dict:
        """Update a road as it gets paved — typically status, decided_approach, home, adr_ref, last_validated_at, validation_note. The slug is immutable."""
        if status is not None and status not in STATUSES:
            return {"error": "invalid_status", "allowed": sorted(STATUSES)}
        if category is not None and category not in CATEGORIES:
            return {"error": "invalid_category", "allowed": sorted(CATEGORIES)}
        validated_at = None
        if last_validated_at is not None:
            validated_at = _parse_ts(last_validated_at)
            if validated_at is None:
                return {"error": "invalid_timestamp", "hint": "last_validated_at must be ISO-8601, e.g. 2026-06-30T12:00:00Z"}
        fields = {
            k: v
            for k, v in {
                "status": status, "decided_approach": decided_approach, "home": home,
                "owner_standard": owner_standard, "adr_ref": adr_ref,
                "last_validated_at": validated_at, "validation_note": validation_note,
                "name": name, "summary": summary, "category": category,
            }.items()
            if v is not None
        }
        if not fields:
            return {"error": "no_fields", "hint": "Provide at least one field to update."}
        async with get_session_factory()() as session:
            repo = RoadRepository(session)
            updated = await repo.update(slug, fields)
            if updated is None:
                return {"error": "not_found", "slug": slug}
            await session.commit()
            return {"updated": True, "slug": updated.slug, "status": updated.status}
