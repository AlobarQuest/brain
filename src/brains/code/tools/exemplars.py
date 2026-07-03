from fastmcp import FastMCP

from src.brains.code.repositories.exemplars import ExemplarRepository
from src.brains.code.repositories.roads import RoadRepository
from src.core.db import get_session_factory
from src.core.governance import proposed_defaults


def register_exemplar_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def add_exemplar(
        road_slug: str,
        label: str,
        location: str,
        note: str | None = None,
        proposed_by: str | None = None,
        auto_approve: bool = False,
    ) -> dict:
        """Propose a canonical example for a road — the 'the clean instance to imitate is here'
        pointer (status=proposed by default; approved only with the approver key and
        auto_approve=True). location is a repo path or URL."""
        async with get_session_factory()() as session:
            if await RoadRepository(session).get_by_slug(road_slug) is None:
                return {"error": "unknown_road", "road_slug": road_slug}
            data = {
                "road_slug": road_slug, "label": label, "location": location, "note": note,
            }
            data.update(
                proposed_defaults(
                    proposed_by=proposed_by,
                    applicability={"road_slug": road_slug},
                    auto_approve=auto_approve,
                )
            )
            e = await ExemplarRepository(session).add(data)
            await session.commit()
            return {"created": True, "id": e.id, "road_slug": e.road_slug, "status": e.status}
