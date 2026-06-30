from fastmcp import FastMCP

from src.core.db import get_session_factory
from src.brains.code.repositories.lessons import LessonRepository
from src.brains.code.repositories.roads import RoadRepository


def register_lesson_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def add_lesson(
        title: str,
        content: str,
        road_slug: str | None = None,
        tags: list[str] | None = None,
        source_app: str | None = None,
    ) -> dict:
        """Add a lesson learned about a code pattern. road_slug=None means a general lesson not tied to one road."""
        async with get_session_factory()() as session:
            if road_slug is not None:
                if await RoadRepository(session).get_by_slug(road_slug) is None:
                    return {"error": "unknown_road", "road_slug": road_slug}
            l = await LessonRepository(session).add({
                "title": title, "content": content, "road_slug": road_slug,
                "tags": tags or [], "source_app": source_app,
            })
            await session.commit()
            return {"created": True, "id": l.id, "title": l.title}
