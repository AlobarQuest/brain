from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.code.models import Road


class RoadRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(
        self,
        category: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[Road]:
        stmt = select(Road)
        if category:
            stmt = stmt.where(Road.category == category)
        if status:
            stmt = stmt.where(Road.status == status)
        stmt = stmt.order_by(Road.category, Road.slug).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_slug(self, slug: str) -> Road | None:
        result = await self.session.execute(select(Road).where(Road.slug == slug))
        return result.scalar_one_or_none()

    async def add(self, data: dict) -> Road:
        road = Road(**data)
        self.session.add(road)
        await self.session.flush()
        return road

    async def add_if_not_exists(self, data: dict) -> int:
        """Insert a road, silently skip if the slug already exists (race-safe seeding).

        Returns the number of rows actually inserted (1 if new, 0 if it conflicted).
        """
        stmt = insert(Road).values(**data).on_conflict_do_nothing(index_elements=["slug"])
        result = await self.session.execute(stmt)
        return result.rowcount or 0

    _UPDATABLE_FIELDS = {
        "name",
        "category",
        "status",
        "summary",
        "decided_approach",
        "home",
        "owner_standard",
        "adr_ref",
        "last_validated_at",
        "validation_note",
    }

    async def update(self, slug: str, fields: dict) -> Road | None:
        """Apply allowed fields to a road. The slug is immutable. Returns None if not found."""
        road = await self.get_by_slug(slug)
        if road is None:
            return None
        for key, value in fields.items():
            if key in self._UPDATABLE_FIELDS:
                setattr(road, key, value)
        road.updated_at = datetime.now(timezone.utc)
        await self.session.flush()
        return road
