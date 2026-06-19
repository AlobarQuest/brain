from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.infra.models import Combo


class ComboRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_name(self, name: str) -> Combo | None:
        """Get a combo by name."""
        result = await self.session.execute(
            select(Combo).where(Combo.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        ecosystem: str | None = None,
        flavor: str | None = None,
        limit: int = 100,
    ) -> list[Combo]:
        """List all combos, optionally filtered by ecosystem or flavor."""
        stmt = select(Combo)
        if ecosystem:
            stmt = stmt.where(Combo.ecosystem == ecosystem)
        if flavor:
            stmt = stmt.where(Combo.flavor == flavor)
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
