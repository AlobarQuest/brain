from fastmcp import FastMCP

from src.brains.code.repositories.lessons import LessonRepository
from src.brains.code.repositories.roads import RoadRepository
from src.core.db import get_session_factory
from src.core.governance import proposed_defaults


def register_lesson_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def add_lesson(
        title: str,
        content: str,
        road_slug: str | None = None,
        tags: list[str] | None = None,
        source_app: str | None = None,
        proposed_by: str | None = None,
        auto_approve: bool = False,
    ) -> dict:
        """Propose a lesson learned about a code pattern (status=proposed by default; approved
        only with the approver key and auto_approve=True). road_slug=None means a general lesson
        not tied to one road."""
        async with get_session_factory()() as session:
            if road_slug is not None:
                if await RoadRepository(session).get_by_slug(road_slug) is None:
                    return {"error": "unknown_road", "road_slug": road_slug}
            data = {
                "title": title,
                "content": content,
                "road_slug": road_slug,
                "tags": tags or [],
                "source_app": source_app,
            }
            data.update(
                proposed_defaults(
                    proposed_by=proposed_by,
                    applicability={"road_slug": road_slug},
                    auto_approve=auto_approve,
                )
            )
            lesson = await LessonRepository(session).add(data)
            await session.commit()
            return {
                "created": True,
                "id": lesson.id,
                "title": lesson.title,
                "status": lesson.status,
            }
