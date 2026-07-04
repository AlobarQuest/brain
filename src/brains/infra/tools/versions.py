from fastmcp import FastMCP

from src.brains.infra.repositories.versions import VersionRepository
from src.core.db import get_session_factory


def register_version_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_version(package: str) -> dict:
        """Get the canonical version record for a package. Use this to check what version is approved before adding a dependency."""
        async with get_session_factory()() as session:
            repo = VersionRepository(session)
            v = await repo.get_by_package(package)
            if not v:
                return {"error": "not_found", "package": package}
            return {
                "package": v.package,
                "canonical": v.canonical,
                "min_allowed": v.min_allowed,
                "blocked_above": v.blocked_above,
                "reason": v.reason,
                "confirmed_in": v.confirmed_in or [],
                "ecosystem": v.ecosystem,
                "updated_at": v.updated_at.isoformat() if v.updated_at else None,
                "updated_by": v.updated_by,
            }

    @mcp.tool()
    async def list_versions(ecosystem: str | None = None, limit: int = 100) -> dict:
        """List all version records, optionally filtered by ecosystem. Returns approved package versions for dependency management."""
        limit = max(1, min(limit, 100))
        async with get_session_factory()() as session:
            repo = VersionRepository(session)
            versions = await repo.list_all(ecosystem=ecosystem, limit=limit)
            results = [
                {
                    "package": v.package,
                    "canonical": v.canonical,
                    "min_allowed": v.min_allowed,
                    "blocked_above": v.blocked_above,
                    "reason": v.reason,
                    "confirmed_in": v.confirmed_in or [],
                    "ecosystem": v.ecosystem,
                }
                for v in versions
            ]
            return {"versions": results}

    @mcp.tool()
    async def update_version(
        package: str,
        canonical: str,
        reason: str | None = None,
        confirmed_in: list[str] | None = None,
    ) -> dict:
        """Update the canonical version for an existing package. Use add_version to create new entries."""
        async with get_session_factory()() as session:
            repo = VersionRepository(session)
            existing = await repo.get_by_package(package)
            if not existing:
                return {
                    "error": "not_found",
                    "package": package,
                    "hint": "Use add_version to create new packages",
                }
            data: dict = {"package": package, "canonical": canonical}
            if reason is not None:
                data["reason"] = reason
            if confirmed_in is not None:
                data["confirmed_in"] = confirmed_in
            v = await repo.upsert(data)
            await session.commit()
            return {"updated": True, "package": v.package, "canonical": v.canonical}

    @mcp.tool()
    async def add_version(
        package: str,
        canonical: str,
        ecosystem: str,
        reason: str,
        min_allowed: str | None = None,
        blocked_above: str | None = None,
        confirmed_in: list[str] | None = None,
    ) -> dict:
        """Add a new package to the version registry. Used to track approved dependency versions across the infrastructure."""
        async with get_session_factory()() as session:
            repo = VersionRepository(session)
            data = {
                "package": package,
                "canonical": canonical,
                "ecosystem": ecosystem,
                "reason": reason,
                "min_allowed": min_allowed,
                "blocked_above": blocked_above,
                "confirmed_in": confirmed_in or [],
            }
            v = await repo.upsert(data)
            await session.commit()
            return {"created": True, "package": v.package, "canonical": v.canonical}
