from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.code.models import Exemplar


class ExemplarRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_road(self, road_slug: str) -> list[Exemplar]:
        result = await self.session.execute(
            select(Exemplar).where(Exemplar.road_slug == road_slug)
        )
        return list(result.scalars().all())

    async def add(self, data: dict) -> Exemplar:
        exemplar = Exemplar(**data)
        self.session.add(exemplar)
        await self.session.flush()
        return exemplar
