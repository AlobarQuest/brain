from fastmcp import FastMCP

from src.brains.code.repositories.search import SearchRepository
from src.brains.code.tools.serialize import lesson_dict, road_dict, rule_dict
from src.core.db import get_session_factory


def register_search_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search(
        query: str,
        tags: list[str] | None = None,
        limit: int = 50,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> dict:
        """Keyword search across roads, rules, and lessons.

        Use to discover the relevant paved road and its rules when you don't know
        the exact slug. Rule/lesson results default to approved-only (deprecated/superseded
        always excluded); include_proposed=True also surfaces proposed rules/lessons.
        min_authority filters rule/lesson results to authority >= the given rank
        (informational < recommended < required). Roads are ungoverned and unfiltered.
        """
        limit = max(1, min(limit, 50))
        async with get_session_factory()() as session:
            results = await SearchRepository(session).search(
                query=query, tags=tags, limit=limit,
                include_proposed=include_proposed, min_authority=min_authority,
            )
            return {
                "roads": [road_dict(r) for r in results["roads"]],
                "rules": [rule_dict(r) for r in results["rules"]],
                "lessons": [lesson_dict(lesson) for lesson in results["lessons"]],
            }
