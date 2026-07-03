from fastmcp import FastMCP

from src.core.db import get_session_factory
from src.brains.infra.repositories.combos import ComboRepository


def register_combo_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_combo(name: str) -> dict:
        """Get the full validated package set for a named stack combo. Use this to look up pre-approved dependency sets for a given deployment flavor."""
        async with get_session_factory()() as session:
            repo = ComboRepository(session)
            c = await repo.get_by_name(name)
            if not c:
                return {"error": "not_found", "name": name}
            return {
                "name": c.name,
                "description": c.description,
                "ecosystem": c.ecosystem,
                "flavor": c.flavor,
                "packages": c.packages,
                "confirmed_in": c.confirmed_in or [],
            }

    @mcp.tool()
    async def list_combos(
        ecosystem: str | None = None,
        flavor: str | None = None,
        limit: int = 100,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> dict:
        """List all stack combos, optionally filtered by ecosystem or deployment flavor. Returns
        pre-validated dependency sets. Non-approved (proposed/deprecated/superseded) combos are
        excluded unless include_proposed is set. min_authority filters to authority >= the given
        rank (informational < recommended < required)."""
        limit = max(1, min(limit, 100))
        async with get_session_factory()() as session:
            repo = ComboRepository(session)
            combos = await repo.list_all(
                ecosystem=ecosystem,
                flavor=flavor,
                limit=limit,
                include_proposed=include_proposed,
                min_authority=min_authority,
            )
            results = [
                {
                    "name": c.name,
                    "description": c.description,
                    "ecosystem": c.ecosystem,
                    "flavor": c.flavor,
                    "packages": c.packages,
                    "confirmed_in": c.confirmed_in or [],
                    "status": c.status,
                    "authority": c.authority,
                    "applicability": c.applicability,
                    "conflict": c.conflict_kind,
                }
                for c in combos
            ]
            return {"combos": results}
