from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.code.models import Lesson, Road, Rule
from src.brains.code.repositories._like import escape_like


class SearchRepository:
    """Keyword search across roads, rules, and lessons (mirrors infra's lesson search)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def search(
        self,
        query: str,
        tags: list[str] | None = None,
        limit: int = 50,
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
                or_(Rule.rule.ilike(like), Rule.reason.ilike(like)),
            )
            .limit(limit)
        )
        lessons_stmt = select(Lesson).where(
            or_(Lesson.title.ilike(like), Lesson.content.ilike(like))
        )
        if tags:
            for tag in tags:
                lessons_stmt = lessons_stmt.where(Lesson.tags.any(tag))
        lessons_stmt = lessons_stmt.limit(limit)

        roads = (await self.session.execute(roads_stmt)).scalars().all()
        rules = (await self.session.execute(rules_stmt)).scalars().all()
        lessons = (await self.session.execute(lessons_stmt)).scalars().all()
        return {"roads": list(roads), "rules": list(rules), "lessons": list(lessons)}
