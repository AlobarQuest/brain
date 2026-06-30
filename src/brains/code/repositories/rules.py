from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.code.models import Rule


class RuleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(
        self,
        category: str | None = None,
        severity: str | None = None,
        road_slug: str | None = None,
        limit: int = 100,
        include_retired: bool = False,
    ) -> list[Rule]:
        """List rules, optionally filtered. Excludes retired rules unless include_retired=True."""
        stmt = select(Rule)
        if category:
            stmt = stmt.where(Rule.category == category)
        if severity:
            stmt = stmt.where(Rule.severity == severity)
        if road_slug:
            stmt = stmt.where(Rule.road_slug == road_slug)
        if not include_retired:
            stmt = stmt.where(Rule.retired_at.is_(None))
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, data: dict) -> Rule:
        rule = Rule(**data)
        self.session.add(rule)
        await self.session.flush()
        return rule

    async def add_if_not_exists(self, data: dict) -> int:
        """Insert a rule, silently skip if the rule text already exists (race-safe).

        Returns the number of rows actually inserted (1 if new, 0 if it conflicted).
        """
        stmt = insert(Rule).values(**data).on_conflict_do_nothing(index_elements=["rule"])
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    async def get_by_id(self, rule_id: int) -> Rule | None:
        return await self.session.get(Rule, rule_id)

    async def retire(self, rule_id: int) -> Rule | None:
        """Soft-delete: set retired_at. Idempotent — preserves the original timestamp."""
        rule = await self.get_by_id(rule_id)
        if rule is None:
            return None
        if rule.retired_at is None:
            rule.retired_at = datetime.now(timezone.utc)
        await self.session.flush()
        return rule
