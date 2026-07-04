from fastmcp import FastMCP

from src.brains.code.models import Rule
from src.brains.code.repositories.roads import RoadRepository
from src.brains.code.repositories.rules import RuleRepository
from src.brains.code.tools.serialize import rule_dict
from src.core.db import get_session_factory
from src.core.governance import (
    finalize_governance,
    find_conflicts,
    proposed_defaults,
    require_approver,
)


def register_rule_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_rules(
        category: str | None = None,
        severity: str | None = None,
        road_slug: str | None = None,
        limit: int = 100,
        include_retired: bool = False,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> dict:
        """Get code-standard rules an agent checks before/while coding, optionally filtered by
        category, severity, and/or road_slug. Retired rules are excluded unless
        include_retired=True. Non-approved (proposed/deprecated/superseded) rules are excluded
        unless include_proposed=True. min_authority filters to authority >= the given rank
        (informational < recommended < required). Always check severity='BLOCK' first."""
        limit = max(1, min(limit, 100))
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            rules = await repo.list_all(
                category=category,
                severity=severity,
                road_slug=road_slug,
                limit=limit,
                include_retired=include_retired,
                include_proposed=include_proposed,
                min_authority=min_authority,
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
        proposed_by: str | None = None,
        auto_approve: bool = False,
    ) -> dict:
        """Propose a normative rule for a road (status=proposed by default; approved only with
        the approver key and auto_approve=True). severity must be BLOCK, WARN, or INFO. Provide
        a machine-checkable `check` (forbidden_pattern/required_pattern/manifest_present/
        judgment/...) where possible so a scanner can later consume it. A candidate flagged as a
        duplicate of an approved required/recommended rule cannot auto-approve; an overlap is
        advisory. A judgment-kind check is opaque and never flagged as a duplicate."""
        if severity not in ("BLOCK", "WARN", "INFO"):
            return {"error": "invalid_severity", "allowed": ["BLOCK", "WARN", "INFO"]}
        applicability = {"road_slug": road_slug, "category": category}
        data = {
            "road_slug": road_slug,
            "severity": severity,
            "category": category,
            "rule": rule,
            "reason": reason,
            "source": source,
            "check": check,
            "good_example": good_example,
            "bad_example": bad_example,
        }
        data.update(
            proposed_defaults(
                proposed_by=proposed_by, applicability=applicability, auto_approve=auto_approve
            )
        )
        async with get_session_factory()() as session:
            if await RoadRepository(session).get_by_slug(road_slug) is None:
                return {"error": "unknown_road", "road_slug": road_slug}
            # A judgment-kind check is opaque (no fixed expected shape) — never a duplicate.
            candidate_check = check if (check and check.get("kind") != "judgment") else None
            flag = await find_conflicts(
                session,
                Rule,
                candidate_check=candidate_check,
                overlap_key_fields=("road_slug", "category"),
                candidate={"road_slug": road_slug, "category": category},
            )
            finalize_governance(data, flag)  # duplicate cancels auto-approve; overlap is advisory
            r = await RuleRepository(session).add(data)
            await session.commit()
            return {
                "created": True,
                "id": r.id,
                "road_slug": r.road_slug,
                "severity": r.severity,
                "status": r.status,
                "conflict": flag.kind if flag else None,
            }

    @mcp.tool()
    async def retire_rule(id: int) -> dict:
        """Soft-delete (retire) a rule by id. APPROVER KEY ONLY.

        Retired rules are excluded from get_rules by default. Idempotent.
        """
        if not require_approver():
            return {"error": "not_authorized", "hint": "retire requires the approver key"}
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            existing = await repo.get_by_id(id)
            if existing is None:
                return {"error": "not_found", "id": id}
            already_retired = existing.retired_at is not None
            await repo.retire(id)
            await session.commit()
            return {"retired": True, "id": id, "already_retired": already_retired}
