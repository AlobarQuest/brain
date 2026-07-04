import uuid

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.app.models import AppKnowledge
from src.core.governance import (
    AUTHORITY_RANK,
    STATUS_APPROVED,
    STATUS_DEPRECATED,
    STATUS_PROPOSED,
    STATUS_SUPERSEDED,
)


def _allowed_statuses(include_proposed: bool) -> list[str]:
    return [STATUS_APPROVED] + ([STATUS_PROPOSED] if include_proposed else [])


def _allowed_authorities(min_authority: str) -> list[str]:
    """Callers only invoke this under an `if min_authority:` truthy guard."""
    return [a for a, rank in AUTHORITY_RANK.items() if rank >= AUTHORITY_RANK[min_authority]]


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
    ) -> AppKnowledge | None:
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

    async def get_by_id(self, chunk_id: uuid.UUID) -> AppKnowledge | None:
        result = await self.session.execute(select(AppKnowledge).where(AppKnowledge.id == chunk_id))
        return result.scalar_one_or_none()

    async def deactivate(self, chunk_id: uuid.UUID) -> bool:
        """Soft-delete: mark inactive and deprecate the governance status. Used by both
        delete_knowledge and capture_knowledge's supersedes_id handling — a knowledge chunk
        that is no longer active should not keep surfacing as 'approved'."""
        result = await self.session.execute(
            update(AppKnowledge)
            .where(AppKnowledge.id == chunk_id, AppKnowledge.is_active == True)  # noqa: E712
            .values(is_active=False, status=STATUS_DEPRECATED)
        )
        return result.rowcount > 0

    async def supersede(self, old_chunk_id: uuid.UUID, new_chunk_id: uuid.UUID) -> bool:
        """Soft-delete the OLD row when a new chunk explicitly supersedes it (capture_knowledge's
        supersedes_id path only — a plain delete_knowledge stays on deactivate()). Distinct from
        deactivate(): status becomes 'superseded' (not 'deprecated'), and superseded_by_id points
        at the replacement row, so the supersession chain is queryable."""
        result = await self.session.execute(
            update(AppKnowledge)
            .where(AppKnowledge.id == old_chunk_id, AppKnowledge.is_active == True)  # noqa: E712
            .values(is_active=False, status=STATUS_SUPERSEDED, superseded_by_id=new_chunk_id)
        )
        return result.rowcount > 0

    async def delete_by_id(self, chunk_id: uuid.UUID) -> bool:
        result = await self.session.execute(delete(AppKnowledge).where(AppKnowledge.id == chunk_id))
        return result.rowcount > 0

    async def search_semantic(
        self,
        query_embedding: list[float],
        threshold: float = 0.5,
        limit: int = 10,
        app_slug: str | None = None,
        knowledge_type: str | None = None,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> list[dict]:
        """Semantic search via the match_app_knowledge_semantic() pgvector function, which
        only knows about is_active (not governance status/authority). Over-fetches from the
        function and filters/annotates by governance status in Python — the function's
        signature isn't governance-aware and this avoids widening a plpgsql function for a
        Python-expressible filter."""
        fetch_count = min(limit * 3, 150)
        result = await self.session.execute(
            text(
                "SELECT id, app_slug, knowledge_type, content, metadata, similarity "
                "FROM match_app_knowledge_semantic("
                "CAST(:embedding AS vector), :threshold, :limit, :app_slug, :type_filter)"
            ),
            {
                "embedding": str(query_embedding),
                "threshold": threshold,
                "limit": fetch_count,
                "app_slug": app_slug,
                "type_filter": knowledge_type,
            },
        )
        rows = result.fetchall()
        if not rows:
            return []
        governed = await self._governance_by_id(
            [row.id for row in rows], include_proposed, min_authority
        )
        results = []
        for row in rows:
            g = governed.get(row.id)
            if g is None:
                continue
            results.append(
                {
                    "id": str(row.id),
                    "app_slug": row.app_slug,
                    "knowledge_type": row.knowledge_type,
                    "content": row.content,
                    "metadata": row.metadata,
                    "similarity": row.similarity,
                    "status": g["status"],
                    "authority": g["authority"],
                    "applicability": g["applicability"],
                    "conflict": g["conflict_kind"],
                }
            )
            if len(results) >= limit:
                break
        return results

    async def _governance_by_id(
        self,
        ids: list[uuid.UUID],
        include_proposed: bool,
        min_authority: str | None,
    ) -> dict[uuid.UUID, dict]:
        """Batch-lookup governance fields for a set of ids, pre-filtered to the allowed
        status/authority. Used to post-filter results from the pgvector match function."""
        stmt = select(
            AppKnowledge.id,
            AppKnowledge.status,
            AppKnowledge.authority,
            AppKnowledge.applicability,
            AppKnowledge.conflict_kind,
        ).where(
            AppKnowledge.id.in_(ids),
            AppKnowledge.status.in_(_allowed_statuses(include_proposed)),
        )
        if min_authority:
            stmt = stmt.where(AppKnowledge.authority.in_(_allowed_authorities(min_authority)))
        result = await self.session.execute(stmt)
        return {
            row.id: {
                "status": row.status,
                "authority": row.authority,
                "applicability": row.applicability,
                "conflict_kind": row.conflict_kind,
            }
            for row in result.all()
        }

    async def search_keyword(
        self,
        query: str,
        limit: int = 10,
        app_slug: str | None = None,
        knowledge_type: str | None = None,
        include_proposed: bool = False,
        min_authority: str | None = None,
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
                AppKnowledge.status,
                AppKnowledge.authority,
                AppKnowledge.applicability,
                AppKnowledge.conflict_kind,
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
        stmt = stmt.where(AppKnowledge.status.in_(_allowed_statuses(include_proposed)))
        if min_authority:
            stmt = stmt.where(AppKnowledge.authority.in_(_allowed_authorities(min_authority)))

        result = await self.session.execute(stmt)
        return [
            {
                "id": str(row.id),
                "app_slug": row.app_slug,
                "knowledge_type": row.knowledge_type,
                "content": row.content,
                "metadata": row.metadata_,
                "similarity": float(row.rank),
                "status": row.status,
                "authority": row.authority,
                "applicability": row.applicability,
                "conflict": row.conflict_kind,
            }
            for row in result.all()
        ]

    async def list_knowledge(
        self,
        app_slug: str,
        knowledge_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
        active_only: bool = True,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> list[dict]:
        """List knowledge chunks. active_only (default True) filters is_active; the governance
        status filter (default approved-only) is applied in addition — pass include_proposed=True
        to also include proposed chunks, and min_authority to filter to authority >= the given
        rank."""
        stmt = (
            select(
                AppKnowledge.id,
                AppKnowledge.app_slug,
                AppKnowledge.knowledge_type,
                AppKnowledge.content,
                AppKnowledge.source,
                AppKnowledge.is_active,
                AppKnowledge.created_at,
                AppKnowledge.status,
                AppKnowledge.authority,
                AppKnowledge.applicability,
                AppKnowledge.conflict_kind,
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
        stmt = stmt.where(AppKnowledge.status.in_(_allowed_statuses(include_proposed)))
        if min_authority:
            stmt = stmt.where(AppKnowledge.authority.in_(_allowed_authorities(min_authority)))

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
                "status": row.status,
                "authority": row.authority,
                "applicability": row.applicability,
                "conflict": row.conflict_kind,
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
        Optionally exclude specific IDs (e.g., newly created chunks). Also deprecates governance
        status (consistent with deactivate()) so a replaced onboard chunk stops surfacing as
        'approved' — combined with onboarding landing new chunks approved, a re-onboard yields no
        knowledge blackout: old chunks deprecated+hidden, new chunks approved+visible."""
        stmt = (
            update(AppKnowledge)
            .where(
                AppKnowledge.app_slug == app_slug,
                AppKnowledge.source == "onboard",
                AppKnowledge.is_active == True,  # noqa: E712
            )
            .values(is_active=False, status=STATUS_DEPRECATED)
        )
        if exclude_ids:
            stmt = stmt.where(AppKnowledge.id.notin_(exclude_ids))
        result = await self.session.execute(stmt)
        return result.rowcount
