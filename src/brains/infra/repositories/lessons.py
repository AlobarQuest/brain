from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.infra.models import Lesson


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE special characters to prevent wildcard injection."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class LessonRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def search(
        self,
        query: str,
        app: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
    ) -> list[Lesson]:
        """Search lessons by keyword with ILIKE, optionally filtered by app and tags."""
        escaped = _escape_like(query)
        stmt = select(Lesson).where(
            or_(
                Lesson.title.ilike(f"%{escaped}%"),
                Lesson.content.ilike(f"%{escaped}%"),
            )
        )
        if app:
            stmt = stmt.where(Lesson.app == app)
        if tags:
            for tag in tags:
                stmt = stmt.where(Lesson.tags.any(tag))
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, data: dict) -> Lesson:
        """Insert a new lesson."""
        lesson = Lesson(**data)
        self.session.add(lesson)
        await self.session.flush()
        return lesson

    async def add_if_not_exists(self, data: dict) -> None:
        """Insert lesson, silently skip if the title already exists (race-safe)."""
        stmt = (
            insert(Lesson)
            .values(**data)
            .on_conflict_do_nothing(index_elements=["title"])
        )
        await self.session.execute(stmt)
