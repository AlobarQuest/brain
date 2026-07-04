"""App brain OpenRouter service.

embed() delegates to the shared core embeddings client (src.core.embeddings).
extract_metadata() is app-local: same endpoint/model/prompt as the source app-brain.
"""

import json

import httpx

from src.core.config import get_settings
from src.core.embeddings import get_embeddings_client

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

METADATA_SYSTEM_PROMPT = """Extract metadata from this knowledge chunk. Return JSON with:
- "topics": array of 1-3 short topic tags (always at least one)
- "entities": array of named things mentioned: tools, services, people, systems (empty if none)
- "tags": array of 1-3 broader category tags
Only extract what's explicitly there."""
DEFAULT_METADATA = {"topics": ["uncategorized"], "entities": [], "tags": []}


def normalize_metadata(value: object) -> dict:
    """Return the stable metadata shape downstream search and aggregation expect."""
    if not isinstance(value, dict):
        return dict(DEFAULT_METADATA)

    normalized: dict[str, list[str]] = {}
    for key, minimum, maximum in (("topics", 1, 3), ("entities", 0, 50), ("tags", 0, 3)):
        items = value.get(key)
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            return dict(DEFAULT_METADATA)
        cleaned = [item.strip() for item in items if item.strip()]
        if len(cleaned) < minimum:
            return dict(DEFAULT_METADATA)
        normalized[key] = cleaned[:maximum]
    return normalized


async def embed(text: str) -> list[float]:
    client = get_embeddings_client(get_settings())
    if client is None:
        raise RuntimeError("openrouter_api_key is required for the app brain")
    return await client.embed(text)


async def _post_openrouter(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            f"{OPENROUTER_BASE}{path}",
            headers={
                "Authorization": f"Bearer {get_settings().openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        if not r.is_success:
            raise RuntimeError(f"OpenRouter request failed: {r.status_code} {r.text}")
        return r.json()


async def extract_metadata(text: str) -> dict:
    data = await _post_openrouter(
        "/chat/completions",
        {
            "model": "openai/gpt-4o-mini",
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": METADATA_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
        },
    )
    try:
        return normalize_metadata(json.loads(data["choices"][0]["message"]["content"]))
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return dict(DEFAULT_METADATA)
