"""OpenRouter embeddings client (lazy init).

Ported from open-brain/src/services/openrouter.py.
Request shape: POST /embeddings with model=openai/text-embedding-3-small and input=<text>.
Response shape: {"data": [{"embedding": [float, ...]}]}.
"""

from __future__ import annotations

import httpx

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
EMBED_MODEL = "openai/text-embedding-3-small"


class EmbeddingsClient:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def embed(self, text: str) -> list[float]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{OPENROUTER_BASE}/embeddings",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": EMBED_MODEL,
                    "input": text,
                },
            )
            if not r.is_success:
                msg = r.text
                raise RuntimeError(f"OpenRouter request failed: {r.status_code} {msg}")
            data = r.json()
            return data["data"][0]["embedding"]


def get_embeddings_client(settings) -> EmbeddingsClient | None:
    """Return an EmbeddingsClient when an API key is configured, else None.

    Args:
        settings: A src.core.config.Settings instance.

    Returns:
        EmbeddingsClient if openrouter_api_key is set, otherwise None.
    """
    if settings.openrouter_api_key is None:
        return None
    return EmbeddingsClient(api_key=settings.openrouter_api_key)
