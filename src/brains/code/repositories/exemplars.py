from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.code.models import Exemplar
from src.core.governance import AUTHORITY_RANK, STATUS_APPROVED, STATUS_PROPOSED


class ExemplarRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_for_road(
        self,
        road_slug: str,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> list[Exemplar]:
        """List exemplars for a road. Defaults to approved exemplars only (excludes
        proposed/deprecated/superseded); pass include_proposed=True to also include proposed
        exemplars. min_authority filters to authority >= the given rank."""
        stmt = select(Exemplar).where(Exemplar.road_slug == road_slug)
        allowed_statuses = [STATUS_APPROVED] + ([STATUS_PROPOSED] if include_proposed else [])
        stmt = stmt.where(Exemplar.status.in_(allowed_statuses))
        if min_authority:
            allowed_authorities = [
                a for a, rank in AUTHORITY_RANK.items() if rank >= AUTHORITY_RANK[min_authority]
            ]
            stmt = stmt.where(Exemplar.authority.in_(allowed_authorities))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, data: dict) -> Exemplar:
        exemplar = Exemplar(**data)
        self.session.add(exemplar)
        await self.session.flush()
        return exemplar
