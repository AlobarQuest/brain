from fastmcp import FastMCP

from src.core.db import get_session_factory
from src.brains.code.repositories.search import SearchRepository
from src.brains.code.tools.serialize import road_dict, rule_dict, lesson_dict


def register_search_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search(query: str, tags: list[str] | None = None, limit: int = 50) -> dict:
        """Keyword search across roads, rules, and lessons. Use to discover the relevant paved road and its rules when you don't know the exact slug."""
        limit = max(1, min(limit, 50))
        async with get_session_factory()() as session:
            results = await SearchRepository(session).search(query=query, tags=tags, limit=limit)
            return {
                "roads": [road_dict(r) for r in results["roads"]],
                "rules": [rule_dict(r) for r in results["rules"]],
                "lessons": [lesson_dict(l) for l in results["lessons"]],
            }
