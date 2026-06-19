import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.app.models import App


class AppRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_apps(
        self,
        status: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> list[dict]:
        stmt = (
            select(App.slug, App.name, App.status, App.onboarding_status, App.description, App.tags)
            .order_by(App.name)
        )
        if status:
            stmt = stmt.where(App.status == status)
        if tags:
            stmt = stmt.where(App.tags.overlap(tags))

        result = await self.session.execute(stmt)
        return [row._asdict() for row in result.all()]

    async def get_app(self, slug: str) -> Optional[App]:
        result = await self.session.execute(
            select(App).where(App.slug == slug)
        )
        return result.scalar_one_or_none()

    async def create_app(self, **kwargs) -> App:
        app = App(**kwargs)
        self.session.add(app)
        await self.session.flush()
        return app

    async def update_app(self, slug: str, **fields) -> Optional[App]:
        app = await self.get_app(slug)
        if not app:
            return None
        for key, value in fields.items():
            setattr(app, key, value)
        await self.session.flush()
        return app

    async def mark_onboarding_status(
        self,
        slug: str,
        status: str,
        error: Optional[str] = None,
        onboarded_at: Optional[datetime] = None,
    ) -> None:
        values: dict = {"onboarding_status": status, "last_onboarding_error": error}
        if onboarded_at:
            values["last_onboarded_at"] = onboarded_at
        await self.session.execute(
            update(App).where(App.slug == slug).values(**values)
        )

    async def fail_stale_running(self) -> int:
        """Mark any app stuck in 'running' (from an interrupted job) as 'failed'."""
        result = await self.session.execute(
            update(App)
            .where(App.onboarding_status == "running")
            .values(
                onboarding_status="failed",
                last_onboarding_error="onboarding interrupted by restart",
            )
        )
        return result.rowcount
