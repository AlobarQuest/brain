import uuid
from typing import Optional

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.app.models import AppKnowledge


class KnowledgeRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> AppKnowledge:
        chunk = AppKnowledge(**kwargs)
        self.session.add(chunk)
        await self.session.flush()
        return chunk

    async def find_duplicate(
        self,
        app_slug: str,
        knowledge_type: str,
        content_hash: str,
    ) -> Optional[AppKnowledge]:
        result = await self.session.execute(
            select(AppKnowledge)
            .where(
                AppKnowledge.app_slug == app_slug,
                AppKnowledge.knowledge_type == knowledge_type,
                AppKnowledge.content_hash == content_hash,
                AppKnowledge.is_active == True,  # noqa: E712
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, chunk_id: uuid.UUID) -> Optional[AppKnowledge]:
        result = await self.session.execute(
            select(AppKnowledge).where(AppKnowledge.id == chunk_id)
        )
        return result.scalar_one_or_none()

    async def deactivate(self, chunk_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            update(AppKnowledge)
            .where(AppKnowledge.id == chunk_id, AppKnowledge.is_active == True)  # noqa: E712
            .values(is_active=False)
        )
        return result.rowcount > 0

    async def delete_by_id(self, chunk_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(AppKnowledge).where(AppKnowledge.id == chunk_id)
        )
        return result.rowcount > 0

    async def search_semantic(
        self,
        query_embedding: list[float],
        threshold: float = 0.5,
        limit: int = 10,
        app_slug: Optional[str] = None,
        knowledge_type: Optional[str] = None,
    ) -> list[dict]:
        result = await self.session.execute(
            text(
                "SELECT id, app_slug, knowledge_type, content, metadata, similarity "
                "FROM match_app_knowledge_semantic("
                "CAST(:embedding AS vector), :threshold, :limit, :app_slug, :type_filter)"
            ),
            {
                "embedding": str(query_embedding),
                "threshold": threshold,
                "limit": limit,
                "app_slug": app_slug,
                "type_filter": knowledge_type,
            },
        )
        return [
            {
                "id": str(row.id),
                "app_slug": row.app_slug,
                "knowledge_type": row.knowledge_type,
                "content": row.content,
                "metadata": row.metadata,
                "similarity": row.similarity,
            }
            for row in result.fetchall()
        ]

    async def search_keyword(
        self,
        query: str,
        limit: int = 10,
        app_slug: Optional[str] = None,
        knowledge_type: Optional[str] = None,
    ) -> list[dict]:
        ts_vector = func.to_tsvector("simple", AppKnowledge.content)
        ts_query = func.plainto_tsquery("simple", query)
        rank = func.ts_rank_cd(ts_vector, ts_query).label("rank")

        stmt = (
            select(
                AppKnowledge.id,
                AppKnowledge.app_slug,
                AppKnowledge.knowledge_type,
                AppKnowledge.content,
                AppKnowledge.metadata_,
                rank,
            )
            .where(
                AppKnowledge.is_active == True,  # noqa: E712
                ts_vector.op("@@")(ts_query),
            )
            .order_by(rank.desc())
            .limit(limit)
        )
        if app_slug:
            stmt = stmt.where(AppKnowledge.app_slug == app_slug)
        if knowledge_type:
            stmt = stmt.where(AppKnowledge.knowledge_type == knowledge_type)

        result = await self.session.execute(stmt)
        return [
            {
                "id": str(row.id),
                "app_slug": row.app_slug,
                "knowledge_type": row.knowledge_type,
                "content": row.content,
                "metadata": row.metadata_,
                "similarity": float(row.rank),
            }
            for row in result.all()
        ]

    async def list_knowledge(
        self,
        app_slug: str,
        knowledge_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        active_only: bool = True,
    ) -> list[dict]:
        stmt = (
            select(
                AppKnowledge.id,
                AppKnowledge.app_slug,
                AppKnowledge.knowledge_type,
                AppKnowledge.content,
                AppKnowledge.source,
                AppKnowledge.is_active,
                AppKnowledge.created_at,
            )
            .where(AppKnowledge.app_slug == app_slug)
            .order_by(AppKnowledge.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if active_only:
            stmt = stmt.where(AppKnowledge.is_active == True)  # noqa: E712
        if knowledge_type:
            stmt = stmt.where(AppKnowledge.knowledge_type == knowledge_type)

        result = await self.session.execute(stmt)
        return [
            {
                "id": str(row.id),
                "app_slug": row.app_slug,
                "knowledge_type": row.knowledge_type,
                "content": row.content,
                "source": row.source,
                "is_active": row.is_active,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in result.all()
        ]

    async def count_by_type(self, app_slug: str) -> dict[str, int]:
        result = await self.session.execute(
            select(AppKnowledge.knowledge_type, func.count())
            .where(
                AppKnowledge.app_slug == app_slug,
                AppKnowledge.is_active == True,  # noqa: E712
            )
            .group_by(AppKnowledge.knowledge_type)
        )
        return {row[0]: row[1] for row in result.all()}

    async def deactivate_all_for_app(self, app_slug: str) -> int:
        result = await self.session.execute(
            update(AppKnowledge)
            .where(
                AppKnowledge.app_slug == app_slug,
                AppKnowledge.is_active == True,  # noqa: E712
            )
            .values(is_active=False)
        )
        return result.rowcount

    async def deactivate_onboard_chunks(
        self,
        app_slug: str,
        exclude_ids: list[uuid.UUID] | None = None,
    ) -> int:
        """Deactivate only source='onboard' chunks for an app, preserving manual/ai-capture chunks.
        Optionally exclude specific IDs (e.g., newly created chunks)."""
        stmt = (
            update(AppKnowledge)
            .where(
                AppKnowledge.app_slug == app_slug,
                AppKnowledge.source == "onboard",
                AppKnowledge.is_active == True,  # noqa: E712
            )
            .values(is_active=False)
        )
        if exclude_ids:
            stmt = stmt.where(AppKnowledge.id.notin_(exclude_ids))
        result = await self.session.execute(stmt)
        return result.rowcount
