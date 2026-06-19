from fastmcp import FastMCP

from src.core.db import get_session_factory
from src.brains.infra.repositories.lessons import LessonRepository


def register_lesson_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search_lessons(
        query: str,
        app: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
    ) -> dict:
        """Search lessons by keyword across titles and content. Always call this before working on a known-problematic area to check for prior lessons learned."""
        limit = max(1, min(limit, 50))
        async with get_session_factory()() as session:
            repo = LessonRepository(session)
            lessons = await repo.search(query=query, app=app, tags=tags, limit=limit)
            results = [
                {
                    "id": l.id,
                    "app": l.app,
                    "title": l.title,
                    "content": l.content,
                    "tags": l.tags or [],
                    "severity": l.severity,
                    "source": l.source,
                }
                for l in lessons
            ]
            return {"lessons": results}

    @mcp.tool()
    async def add_lesson(
        title: str,
        content: str,
        app: str | None = None,
        tags: list[str] | None = None,
        severity: str = "INFO",
    ) -> dict:
        """Add a new lesson to the registry. Use this to capture infrastructure lessons learned — things that went wrong, workarounds discovered, or patterns that should be followed. severity: CRITICAL, WARN, or INFO."""
        if severity not in ("CRITICAL", "WARN", "INFO"):
            return {"error": "invalid_severity", "allowed": ["CRITICAL", "WARN", "INFO"]}
        async with get_session_factory()() as session:
            repo = LessonRepository(session)
            l = await repo.add({
                "title": title,
                "content": content,
                "app": app,
                "tags": tags or [],
                "severity": severity,
            })
            await session.commit()
            return {"created": True, "id": l.id, "title": l.title}
