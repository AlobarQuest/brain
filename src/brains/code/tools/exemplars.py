from fastmcp import FastMCP

from src.core.db import get_session_factory
from src.brains.code.repositories.exemplars import ExemplarRepository
from src.brains.code.repositories.roads import RoadRepository


def register_exemplar_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def add_exemplar(
        road_slug: str,
        label: str,
        location: str,
        note: str | None = None,
    ) -> dict:
        """Add a canonical example for a road — the 'the clean instance to imitate is here' pointer. location is a repo path or URL."""
        async with get_session_factory()() as session:
            if await RoadRepository(session).get_by_slug(road_slug) is None:
                return {"error": "unknown_road", "road_slug": road_slug}
            e = await ExemplarRepository(session).add({
                "road_slug": road_slug, "label": label, "location": location, "note": note,
            })
            await session.commit()
            return {"created": True, "id": e.id, "road_slug": e.road_slug}
