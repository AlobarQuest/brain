from fastmcp import FastMCP

from src.core.db import get_session_factory
from src.brains.code.repositories.rules import RuleRepository
from src.brains.code.repositories.roads import RoadRepository
from src.brains.code.tools.serialize import rule_dict


def register_rule_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_rules(
        category: str | None = None,
        severity: str | None = None,
        road_slug: str | None = None,
        limit: int = 100,
        include_retired: bool = False,
    ) -> dict:
        """Get code-standard rules an agent checks before/while coding, optionally filtered by category, severity, and/or road_slug. Retired rules are excluded unless include_retired=True. Always check severity='BLOCK' first."""
        limit = max(1, min(limit, 100))
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            rules = await repo.list_all(
                category=category, severity=severity, road_slug=road_slug,
                limit=limit, include_retired=include_retired,
            )
            return {"rules": [rule_dict(r) for r in rules]}

    @mcp.tool()
    async def add_rule(
        road_slug: str,
        severity: str,
        category: str,
        rule: str,
        reason: str,
        source: str | None = None,
        check: dict | None = None,
        good_example: str | None = None,
        bad_example: str | None = None,
    ) -> dict:
        """Add a normative rule to a road. severity must be BLOCK, WARN, or INFO. Provide a machine-checkable `check` (forbidden_pattern/required_pattern/manifest_present/judgment/...) where possible so a scanner can later consume it."""
        if severity not in ("BLOCK", "WARN", "INFO"):
            return {"error": "invalid_severity", "allowed": ["BLOCK", "WARN", "INFO"]}
        async with get_session_factory()() as session:
            if await RoadRepository(session).get_by_slug(road_slug) is None:
                return {"error": "unknown_road", "road_slug": road_slug}
            r = await RuleRepository(session).add({
                "road_slug": road_slug, "severity": severity, "category": category,
                "rule": rule, "reason": reason, "source": source, "check": check,
                "good_example": good_example, "bad_example": bad_example,
            })
            await session.commit()
            return {"created": True, "id": r.id, "road_slug": r.road_slug, "severity": r.severity}

    @mcp.tool()
    async def retire_rule(id: int) -> dict:
        """Soft-delete (retire) a rule by id. Retired rules are excluded from get_rules by default. Idempotent."""
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            existing = await repo.get_by_id(id)
            if existing is None:
                return {"error": "not_found", "id": id}
            already_retired = existing.retired_at is not None
            await repo.retire(id)
            await session.commit()
            return {"retired": True, "id": id, "already_retired": already_retired}
