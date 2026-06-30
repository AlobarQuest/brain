from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.code.models import Lesson
from src.brains.code.repositories._like import escape_like


class LessonRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_road(self, road_slug: str, limit: int = 100) -> list[Lesson]:
        result = await self.session.execute(
            select(Lesson).where(Lesson.road_slug == road_slug).limit(limit)
        )
        return list(result.scalars().all())

    async def search(
        self,
        query: str,
        road_slug: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
    ) -> list[Lesson]:
        escaped = escape_like(query)
        stmt = select(Lesson).where(
            or_(
                Lesson.title.ilike(f"%{escaped}%"),
                Lesson.content.ilike(f"%{escaped}%"),
            )
        )
        if road_slug:
            stmt = stmt.where(Lesson.road_slug == road_slug)
        if tags:
            for tag in tags:
                stmt = stmt.where(Lesson.tags.any(tag))
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, data: dict) -> Lesson:
        lesson = Lesson(**data)
        self.session.add(lesson)
        await self.session.flush()
        return lesson

    async def add_if_not_exists(self, data: dict) -> None:
        """Insert a lesson, silently skip if the title already exists (race-safe)."""
        stmt = insert(Lesson).values(**data).on_conflict_do_nothing(index_elements=["title"])
        await self.session.execute(stmt)
