from fastmcp import FastMCP

from src.brains.infra.repositories.environments import EnvironmentRepository
from src.brains.infra.repositories.lessons import LessonRepository
from src.core.address_guard import guard_fields, hold_for_retired, scan
from src.core.db import get_session_factory
from src.core.governance import proposed_defaults, severity_auto_approve


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
        auto_approve: bool | None = None,
    ) -> dict:
        """Propose a new lesson (approved for an approver-key caller unless auto_approve=False;
        proposed for any other caller). A CRITICAL lesson stays proposed unless auto_approve=True.
        Use this to capture infrastructure lessons learned — things that went wrong, workarounds
        discovered, or patterns that should be followed. severity: CRITICAL, WARN, or INFO.
        A lesson naming a retired host address stays proposed whatever the key; name the role
        (e.g. prod-vps) instead of an address."""
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
                proposed_by=proposed_by,
                applicability={"app": app},
                auto_approve=severity_auto_approve(auto_approve, severity),
            )
        )
        async with get_session_factory()() as session:
            hits = scan([title, content], await EnvironmentRepository(session).address_book())
            hold_for_retired(data, hits)
            repo = LessonRepository(session)
            lesson = await repo.add(data)
            await session.commit()
            return {
                "created": True,
                "id": lesson.id,
                "title": lesson.title,
                "status": lesson.status,
                **guard_fields(hits, held=True),
            }
