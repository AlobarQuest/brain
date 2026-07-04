from fastmcp import FastMCP

from src.brains.infra.models import Rule
from src.brains.infra.repositories.rules import RuleRepository
from src.core.db import get_session_factory
from src.core.governance import (
    AUTHORITY_REQUIRED,
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
        limit: int = 100,
        include_retired: bool = False,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> dict:
        """Get infrastructure rules, optionally filtered by category and/or severity. Retired and
        non-approved (proposed/deprecated/superseded) rules are excluded unless
        include_retired/include_proposed is set. min_authority filters to authority >= the given
        rank (informational < recommended < required). Always check severity='BLOCK' before
        deployment tasks."""
        limit = max(1, min(limit, 100))
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            rules = await repo.list_all(
                category=category,
                severity=severity,
                limit=limit,
                include_retired=include_retired,
                include_proposed=include_proposed,
                min_authority=min_authority,
            )
            results = [
                {
                    "id": r.id,
                    "severity": r.severity,
                    "category": r.category,
                    "rule": r.rule,
                    "reason": r.reason,
                    "source_app": r.source_app,
                    "check": r.check,
                    "retired_at": r.retired_at.isoformat() if r.retired_at else None,
                    "status": r.status,
                    "authority": r.authority,
                    "applicability": r.applicability,
                    "conflict": r.conflict_kind,
                }
                for r in rules
            ]
            return {"rules": results}

    @mcp.tool()
    async def add_rule(
        severity: str,
        category: str,
        rule: str,
        reason: str,
        source_app: str | None = None,
        check: dict | None = None,
        proposed_by: str | None = None,
        auto_approve: bool = False,
    ) -> dict:
        """Propose a new infrastructure rule (status=proposed by default; approved only with the
        approver key and auto_approve=True). severity must be BLOCK, WARN, or INFO. Use BLOCK for
        deployment-breaking rules. A candidate flagged as a duplicate of an approved required rule
        cannot auto-approve; an overlap is advisory."""
        if severity not in ("BLOCK", "WARN", "INFO"):
            return {"error": "invalid_severity", "allowed": ["BLOCK", "WARN", "INFO"]}
        applicability = {"category": category, "source_app": source_app}
        data = {
            "severity": severity,
            "category": category,
            "rule": rule,
            "reason": reason,
            "source_app": source_app,
            "check": check,
        }
        data.update(
            proposed_defaults(
                proposed_by=proposed_by, applicability=applicability, auto_approve=auto_approve
            )
        )
        async with get_session_factory()() as session:
            flag = await find_conflicts(
                session,
                Rule,
                candidate_check=check,
                overlap_key_fields=("category", "source_app"),
                candidate={"category": category, "source_app": source_app},
            )
            finalize_governance(data, flag)  # duplicate cancels auto-approve; overlap is advisory
            repo = RuleRepository(session)
            r = await repo.add(data)
            await session.commit()
            return {
                "created": True,
                "id": r.id,
                "severity": r.severity,
                "category": r.category,
                "status": r.status,
                "conflict": flag.kind if flag else None,
            }

    @mcp.tool()
    async def update_rule(
        id: int,
        severity: str | None = None,
        category: str | None = None,
        reason: str | None = None,
        source_app: str | None = None,
        check: dict | None = None,
        updated_by: str = "ai-capture",
    ) -> dict:
        """Update an existing rule's fields by id. The rule text itself cannot be changed
        (retire + add a new rule instead). severity, if given, must be BLOCK, WARN, or INFO.
        Updating a required-authority rule requires the approver key; informational/
        recommended rules can be updated by a contributor key as before."""
        if severity is not None and severity not in ("BLOCK", "WARN", "INFO"):
            return {"error": "invalid_severity", "allowed": ["BLOCK", "WARN", "INFO"]}
        fields = {
            k: v
            for k, v in {
                "severity": severity,
                "category": category,
                "reason": reason,
                "source_app": source_app,
                "check": check,
            }.items()
            if v is not None
        }
        if not fields:
            return {"error": "no_fields", "hint": "Provide at least one of: severity, category, reason, source_app, check"}
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            existing = await repo.get_by_id(id)
            if existing is None:
                return {"error": "not_found", "id": id}
            if existing.authority == AUTHORITY_REQUIRED and not require_approver():
                return {
                    "error": "not_authorized",
                    "hint": "updating a required-authority rule requires the approver key",
                }
            updated = await repo.update(id, fields, updated_by=updated_by)
            if updated is None:
                return {"error": "not_found", "id": id}
            await session.commit()
            return {"updated": True, "id": updated.id, "severity": updated.severity, "category": updated.category}

    @mcp.tool()
    async def delete_rule(id: int) -> dict:
        """Soft-delete (retire) a rule by id. Retired rules are excluded from get_rules by default.
        Idempotent. Use restore_rule to undo. APPROVER KEY ONLY."""
        if not require_approver():
            return {"error": "not_authorized", "hint": "delete requires the approver key"}
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            existing = await repo.get_by_id(id)
            if existing is None:
                return {"error": "not_found", "id": id}
            already_retired = existing.retired_at is not None
            await repo.retire(id)
            await session.commit()
            return {"retired": True, "id": id, "already_retired": already_retired}

    @mcp.tool()
    async def restore_rule(id: int) -> dict:
        """Restore (un-retire) a soft-deleted rule by id. APPROVER KEY ONLY — restore()
        unconditionally reinstates status=approved, so a contributor-key caller must not be
        able to reach approved via propose->delete->restore."""
        if not require_approver():
            return {"error": "not_authorized", "hint": "restore requires the approver key"}
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            restored = await repo.restore(id)
            if restored is None:
                return {"error": "not_found", "id": id}
            await session.commit()
            return {"restored": True, "id": id}
