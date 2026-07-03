from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql import ColumnElement

from src.brains.code.models import Lesson, Road, Rule
from src.brains.code.repositories._like import escape_like
from src.core.governance import AUTHORITY_RANK, STATUS_APPROVED, STATUS_PROPOSED


class SearchRepository:
    """Keyword search across roads, rules, and lessons (mirrors infra's lesson search).

    Roads are ungoverned (organizational catalog) and are never filtered by governance.
    Rule and lesson results apply the same safe-retrieval defaults as RuleRepository.list_all:
    approved-only by default, optionally widened to include proposed, always excluding
    deprecated/superseded, with an optional min_authority rank floor.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    def _governance_filter(
        self, status_col: InstrumentedAttribute[str], authority_col: InstrumentedAttribute[str],
        include_proposed: bool, min_authority: str | None,
    ) -> list[ColumnElement]:
        allowed_statuses = [STATUS_APPROVED] + ([STATUS_PROPOSED] if include_proposed else [])
        clauses: list[ColumnElement] = [status_col.in_(allowed_statuses)]
        if min_authority:
            allowed_authorities = [
                a for a, rank in AUTHORITY_RANK.items() if rank >= AUTHORITY_RANK[min_authority]
            ]
            clauses.append(authority_col.in_(allowed_authorities))
        return clauses

    async def search(
        self,
        query: str,
        tags: list[str] | None = None,
        limit: int = 50,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> dict:
        like = f"%{escape_like(query)}%"

        roads_stmt = (
            select(Road)
            .where(
                or_(
                    Road.name.ilike(like),
                    Road.summary.ilike(like),
                    Road.slug.ilike(like),
                    Road.decided_approach.ilike(like),
                )
            )
            .limit(limit)
        )
        rules_stmt = (
            select(Rule)
            .where(
                Rule.retired_at.is_(None),
                *self._governance_filter(
                    Rule.status, Rule.authority, include_proposed, min_authority
                ),
                or_(Rule.rule.ilike(like), Rule.reason.ilike(like)),
            )
            .limit(limit)
        )
        lessons_stmt = select(Lesson).where(
            *self._governance_filter(
                Lesson.status, Lesson.authority, include_proposed, min_authority
            ),
            or_(Lesson.title.ilike(like), Lesson.content.ilike(like)),
        )
        if tags:
            for tag in tags:
                lessons_stmt = lessons_stmt.where(Lesson.tags.any(tag))
        lessons_stmt = lessons_stmt.limit(limit)

        roads = (await self.session.execute(roads_stmt)).scalars().all()
        rules = (await self.session.execute(rules_stmt)).scalars().all()
        lessons = (await self.session.execute(lessons_stmt)).scalars().all()
        return {"roads": list(roads), "rules": list(rules), "lessons": list(lessons)}
