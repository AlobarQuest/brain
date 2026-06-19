import asyncio
import json

import httpx
from fastmcp import FastMCP

from src.core.config import get_settings
from src.core.db import get_session_factory
from src.core.embeddings import get_embeddings_client
from src.brains.open.repositories.thoughts import ThoughtRepository

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

METADATA_SYSTEM_PROMPT = """Extract metadata from the user's captured thought. Return JSON with:
- "people": array of people mentioned (empty if none)
- "action_items": array of implied to-dos (empty if none)
- "dates_mentioned": array of dates YYYY-MM-DD (empty if none)
- "topics": array of 1-3 short topic tags (always at least one)
- "type": one of "observation", "task", "idea", "reference", "person_note"
Only extract what's explicitly there."""


async def _extract_metadata(text: str) -> dict:
    """Extract structured metadata from a thought via OpenRouter chat completion."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-4o-mini",
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": METADATA_SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
            },
        )
        if not r.is_success:
            msg = r.text
            raise RuntimeError(f"OpenRouter request failed: {r.status_code} {msg}")
        data = r.json()
    try:
        return json.loads(data["choices"][0]["message"]["content"])
    except (json.JSONDecodeError, KeyError, IndexError):
        return {"topics": ["uncategorized"], "type": "observation"}


def register_thought_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def capture_thought(content: str) -> dict:
        """Save a new thought to the Open Brain. Generates an embedding and extracts metadata automatically. Use this when the user wants to save something to their brain directly from any AI client — notes, insights, decisions, or migrated content from other systems."""
        settings = get_settings()
        client = get_embeddings_client(settings)

        embedding, metadata = await asyncio.gather(
            client.embed(content),
            _extract_metadata(content),
        )
        metadata["source"] = "mcp"

        async with get_session_factory()() as session:
            repo = ThoughtRepository(session)
            await repo.create(
                content=content,
                embedding=embedding,
                metadata=metadata,
            )
            await session.commit()

        confirmation = f"Captured as {metadata.get('type', 'thought')}"
        if topics := metadata.get("topics"):
            confirmation += f" — {', '.join(topics)}"
        if people := metadata.get("people"):
            confirmation += f" | People: {', '.join(people)}"
        if actions := metadata.get("action_items"):
            confirmation += f" | Actions: {'; '.join(actions)}"
        return {"message": confirmation}

    @mcp.tool()
    async def search_thoughts(
        query: str,
        limit: int = 10,
        threshold: float = 0.35,
    ) -> dict:
        """Search captured thoughts by meaning. Use this when the user asks about a topic, person, or idea they've previously captured."""
        limit = max(1, min(limit, 50))
        settings = get_settings()
        client = get_embeddings_client(settings)
        query_embedding = await client.embed(query)

        async with get_session_factory()() as session:
            repo = ThoughtRepository(session)
            results = await repo.search(
                query_embedding=query_embedding,
                threshold=threshold,
                limit=limit,
            )

        if not results:
            return {"message": f'No thoughts found matching "{query}".'}

        formatted = []
        for i, r in enumerate(results):
            m = r["metadata"] or {}
            parts = [
                f"--- Result {i + 1} ({r['similarity'] * 100:.1f}% match) ---",
                f"Captured: {r['created_at'].strftime('%m/%d/%Y') if r['created_at'] else 'unknown'}",
                f"Type: {m.get('type', 'unknown')}",
            ]
            if topics := m.get("topics"):
                parts.append(f"Topics: {', '.join(topics)}")
            if people := m.get("people"):
                parts.append(f"People: {', '.join(people)}")
            if actions := m.get("action_items"):
                parts.append(f"Actions: {'; '.join(actions)}")
            parts.append(f"\n{r['content']}")
            formatted.append("\n".join(parts))

        return {"message": f"Found {len(results)} thought(s):\n\n" + "\n\n".join(formatted)}

    @mcp.tool()
    async def list_thoughts(
        limit: int = 10,
        type: str | None = None,
        topic: str | None = None,
        person: str | None = None,
        days: int | None = None,
    ) -> dict:
        """List recently captured thoughts with optional filters by type, topic, person, or time range. Use this when the user wants to see recent captures or browse by category."""
        limit = max(1, min(limit, 100))
        async with get_session_factory()() as session:
            repo = ThoughtRepository(session)
            thoughts = await repo.list_thoughts(
                limit=limit,
                type_filter=type,
                topic_filter=topic,
                person_filter=person,
                days=days,
            )

        if not thoughts:
            return {"message": "No thoughts found."}

        formatted = []
        for i, t in enumerate(thoughts):
            m = t.metadata_ or {}
            tags = ", ".join(m.get("topics", []))
            line = (
                f"{i + 1}. [{t.created_at.strftime('%m/%d/%Y')}] "
                f"({m.get('type', '??')}{' - ' + tags if tags else ''})\n"
                f"   {t.content}"
            )
            formatted.append(line)

        return {"message": f"{len(thoughts)} recent thought(s):\n\n" + "\n\n".join(formatted)}

    @mcp.tool()
    async def thought_stats() -> dict:
        """Get a summary of all captured thoughts: totals, types, top topics, and people. Use this for an overview of what's stored in the brain."""
        async with get_session_factory()() as session:
            repo = ThoughtRepository(session)
            stats = await repo.stats()

        lines = [
            f"Total thoughts: {stats['total']}",
            f"Date range: {stats['date_range']['earliest'] or 'N/A'} → {stats['date_range']['latest'] or 'N/A'}",
            "",
            "Types:",
        ]
        for t, count in stats["types"].items():
            lines.append(f"  {t}: {count}")

        if stats["topics"]:
            lines.append("\nTop topics:")
            for t, count in stats["topics"].items():
                lines.append(f"  {t}: {count}")

        if stats["people"]:
            lines.append("\nPeople mentioned:")
            for p, count in stats["people"].items():
                lines.append(f"  {p}: {count}")

        return {"message": "\n".join(lines)}
