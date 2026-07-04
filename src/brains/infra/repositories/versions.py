from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.infra.models import Version


class VersionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_package(self, package: str) -> Version | None:
        """Get a version record by package name."""
        result = await self.session.execute(select(Version).where(Version.package == package))
        return result.scalar_one_or_none()

    async def list_all(self, ecosystem: str | None = None, limit: int = 100) -> list[Version]:
        """List all version records, optionally filtered by ecosystem."""
        stmt = select(Version)
        if ecosystem:
            stmt = stmt.where(Version.ecosystem == ecosystem)
        stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def upsert(self, data: dict) -> Version:
        """Create or update a version record by package name."""
        existing = await self.get_by_package(data["package"])
        if existing:
            for key, value in data.items():
                if key != "package" and value is not None:
                    setattr(existing, key, value)
            await self.session.flush()
            return existing
        else:
            version = Version(**data)
            self.session.add(version)
            await self.session.flush()
            return version
