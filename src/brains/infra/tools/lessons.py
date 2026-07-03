from fastmcp import FastMCP

from src.brains.infra.repositories.lessons import LessonRepository
from src.core.db import get_session_factory
from src.core.governance import proposed_defaults


def register_lesson_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search_lessons(
        query: str,
        app: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> dict:
        """Search lessons by keyword across titles and content. Always call this before working
        on a known-problematic area to check for prior lessons learned. Non-approved
        (proposed/deprecated/superseded) lessons are excluded unless include_proposed is set.
        min_authority filters to authority >= the given rank (informational < recommended <
        required)."""
        limit = max(1, min(limit, 50))
        async with get_session_factory()() as session:
            repo = LessonRepository(session)
            lessons = await repo.search(
                query=query,
                app=app,
                tags=tags,
                limit=limit,
                include_proposed=include_proposed,
                min_authority=min_authority,
            )
            results = [
                {
                    "id": l.id,
                    "app": l.app,
                    "title": l.title,
                    "content": l.content,
                    "tags": l.tags or [],
                    "severity": l.severity,
                    "source": l.source,
                    "status": l.status,
                    "authority": l.authority,
                    "applicability": l.applicability,
                    "conflict": l.conflict_kind,
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
        proposed_by: str | None = None,
        auto_approve: bool = False,
    ) -> dict:
        """Propose a new lesson (status=proposed by default; approved only with the approver key
        and auto_approve=True). Use this to capture infrastructure lessons learned — things that
        went wrong, workarounds discovered, or patterns that should be followed. severity:
        CRITICAL, WARN, or INFO."""
        if severity not in ("CRITICAL", "WARN", "INFO"):
            return {"error": "invalid_severity", "allowed": ["CRITICAL", "WARN", "INFO"]}
        data = {
            "title": title,
            "content": content,
            "app": app,
            "tags": tags or [],
            "severity": severity,
        }
        data.update(
            proposed_defaults(
                proposed_by=proposed_by, applicability={"app": app}, auto_approve=auto_approve
            )
        )
        async with get_session_factory()() as session:
            repo = LessonRepository(session)
            lesson = await repo.add(data)
            await session.commit()
            return {
                "created": True,
                "id": lesson.id,
                "title": lesson.title,
                "status": lesson.status,
            }
