import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.brains.open.models import Thought


class ThoughtRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        content: str,
        embedding: list[float],
        metadata: dict,
    ) -> Thought:
        thought = Thought(
            content=content,
            embedding=embedding,
            metadata_=metadata,
        )
        self.session.add(thought)
        await self.session.flush()
        return thought

    async def search(
        self,
        query_embedding: list[float],
        threshold: float = 0.35,
        limit: int = 10,
    ) -> list[dict]:
        """Semantic search via the match_thoughts Postgres function."""
        result = await self.session.execute(
            text(
                "SELECT id, content, metadata, similarity, created_at "
                "FROM match_thoughts(CAST(:embedding AS vector), :threshold, :limit, CAST(:filter AS jsonb))"
            ),
            {
                "embedding": str(query_embedding),
                "threshold": threshold,
                "limit": limit,
                "filter": "{}",
            },
        )
        rows = result.fetchall()
        return [
            {
                "id": row.id,
                "content": row.content,
                "metadata": row.metadata,
                "similarity": row.similarity,
                "created_at": row.created_at,
            }
            for row in rows
        ]

    async def list_thoughts(
        self,
        limit: int = 10,
        type_filter: Optional[str] = None,
        topic_filter: Optional[str] = None,
        person_filter: Optional[str] = None,
        days: Optional[int] = None,
    ) -> list[Thought]:
        stmt = (
            select(Thought)
            .order_by(Thought.created_at.desc())
            .limit(limit)
        )
        if type_filter:
            stmt = stmt.where(Thought.metadata_.contains({"type": type_filter}))
        if topic_filter:
            stmt = stmt.where(Thought.metadata_.contains({"topics": [topic_filter]}))
        if person_filter:
            stmt = stmt.where(Thought.metadata_.contains({"people": [person_filter]}))
        if days:
            since = datetime.now(timezone.utc) - timedelta(days=days)
            stmt = stmt.where(Thought.created_at >= since)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def stats(self) -> dict:
        """Aggregate stats from all thoughts. In-app aggregation, acceptable at ~50 rows."""
        count_result = await self.session.execute(
            select(func.count()).select_from(Thought)
        )
        total = count_result.scalar()

        result = await self.session.execute(
            select(Thought.metadata_, Thought.created_at)
            .order_by(Thought.created_at.desc())
        )
        rows = result.all()

        types: dict[str, int] = {}
        topics: dict[str, int] = {}
        people: dict[str, int] = {}
        dates = []

        for metadata_, created_at in rows:
            m = metadata_ or {}
            dates.append(created_at)
            if t := m.get("type"):
                types[t] = types.get(t, 0) + 1
            for topic in m.get("topics", []):
                topics[topic] = topics.get(topic, 0) + 1
            for person in m.get("people", []):
                people[person] = people.get(person, 0) + 1

        return {
            "total": total,
            "date_range": {
                "earliest": min(dates).isoformat() if dates else None,
                "latest": max(dates).isoformat() if dates else None,
            },
            "types": dict(sorted(types.items(), key=lambda x: -x[1])),
            "topics": dict(sorted(topics.items(), key=lambda x: -x[1])[:10]),
            "people": dict(sorted(people.items(), key=lambda x: -x[1])[:10]),
        }
