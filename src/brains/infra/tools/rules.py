from fastmcp import FastMCP

from src.core.db import get_session_factory
from src.brains.infra.repositories.rules import RuleRepository


def register_rule_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_rules(
        category: str | None = None,
        severity: str | None = None,
        limit: int = 100,
        include_retired: bool = False,
    ) -> dict:
        """Get infrastructure rules, optionally filtered by category and/or severity. Retired rules are excluded unless include_retired=True. Always check severity='BLOCK' before deployment tasks."""
        limit = max(1, min(limit, 100))
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            rules = await repo.list_all(
                category=category, severity=severity, limit=limit, include_retired=include_retired
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
    ) -> dict:
        """Add a new infrastructure rule. severity must be BLOCK, WARN, or INFO. Use BLOCK for deployment-breaking rules."""
        if severity not in ("BLOCK", "WARN", "INFO"):
            return {"error": "invalid_severity", "allowed": ["BLOCK", "WARN", "INFO"]}
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            r = await repo.add({
                "severity": severity,
                "category": category,
                "rule": rule,
                "reason": reason,
                "source_app": source_app,
                "check": check,
            })
            await session.commit()
            return {"created": True, "id": r.id, "severity": r.severity, "category": r.category}

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
        """Update an existing rule's fields by id. The rule text itself cannot be changed (retire + add a new rule instead). severity, if given, must be BLOCK, WARN, or INFO."""
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
            updated = await repo.update(id, fields, updated_by=updated_by)
            if updated is None:
                return {"error": "not_found", "id": id}
            await session.commit()
            return {"updated": True, "id": updated.id, "severity": updated.severity, "category": updated.category}

    @mcp.tool()
    async def delete_rule(id: int) -> dict:
        """Soft-delete (retire) a rule by id. Retired rules are excluded from get_rules by default. Idempotent. Use restore_rule to undo."""
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
        """Restore (un-retire) a soft-deleted rule by id."""
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            restored = await repo.restore(id)
            if restored is None:
                return {"error": "not_found", "id": id}
            await session.commit()
            return {"restored": True, "id": id}
