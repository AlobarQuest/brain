from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.infra.models import Combo
from src.core.governance import AUTHORITY_RANK, STATUS_APPROVED, STATUS_PROPOSED


class ComboRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_name(self, name: str) -> Combo | None:
        """Get a combo by name."""
        result = await self.session.execute(select(Combo).where(Combo.name == name))
        return result.scalar_one_or_none()

    async def list_all(
        self,
        ecosystem: str | None = None,
        flavor: str | None = None,
        limit: int = 100,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> list[Combo]:
        """List all combos, optionally filtered by ecosystem or flavor. Defaults to approved
        combos only (excludes proposed/deprecated/superseded); pass include_proposed=True to
        also include proposed combos. min_authority filters to authority >= the given rank."""
        stmt = select(Combo)
        if ecosystem:
            stmt = stmt.where(Combo.ecosystem == ecosystem)
        if flavor:
            stmt = stmt.where(Combo.flavor == flavor)
        allowed_statuses = [STATUS_APPROVED] + ([STATUS_PROPOSED] if include_proposed else [])
        stmt = stmt.where(Combo.status.in_(allowed_statuses))
        if min_authority:
            allowed_authorities = [
                a for a, rank in AUTHORITY_RANK.items() if rank >= AUTHORITY_RANK[min_authority]
            ]
            stmt = stmt.where(Combo.authority.in_(allowed_authorities))
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
