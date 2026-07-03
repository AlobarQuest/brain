from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.infra.models import Lesson
from src.core.governance import AUTHORITY_RANK, STATUS_APPROVED, STATUS_PROPOSED


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
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> list[Lesson]:
        """Search lessons by keyword with ILIKE, optionally filtered by app and tags.
        Defaults to approved lessons only (excludes proposed/deprecated/superseded); pass
        include_proposed=True to also include proposed lessons. min_authority filters to
        authority >= the given rank."""
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
        allowed_statuses = [STATUS_APPROVED] + ([STATUS_PROPOSED] if include_proposed else [])
        stmt = stmt.where(Lesson.status.in_(allowed_statuses))
        if min_authority:
            allowed_authorities = [
                a for a, rank in AUTHORITY_RANK.items() if rank >= AUTHORITY_RANK[min_authority]
            ]
            stmt = stmt.where(Lesson.authority.in_(allowed_authorities))
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
