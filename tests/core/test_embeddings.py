import pytest
import respx
import httpx

from src.core.config import Settings, BrainType
from src.core.embeddings import EmbeddingsClient, get_embeddings_client

BASE = dict(
    brain_type="open",
    mcp_access_key="a" * 64,
    postgres_host="db",
    postgres_user="u",
    postgres_password="p",
    postgres_db="d",
)

FAKE_VECTOR = [0.1, 0.2, 0.3]
OPENROUTER_EMBED_URL = "https://openrouter.ai/api/v1/embeddings"


@respx.mock
@pytest.mark.asyncio
async def test_embed_returns_vector():
    respx.post(OPENROUTER_EMBED_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"embedding": FAKE_VECTOR}]},
        )
    )
    client = EmbeddingsClient(api_key="test-key")
    result = await client.embed("hello world")
    assert result == FAKE_VECTOR


@respx.mock
@pytest.mark.asyncio
async def test_embed_sends_correct_payload():
    route = respx.post(OPENROUTER_EMBED_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"embedding": FAKE_VECTOR}]},
        )
    )
    client = EmbeddingsClient(api_key="sk-test")
    await client.embed("hello")
    request = route.calls.last.request
    import json
    body = json.loads(request.content)
    assert body["model"] == "openai/text-embedding-3-small"
    assert body["input"] == "hello"
    assert request.headers["authorization"] == "Bearer sk-test"


@respx.mock
@pytest.mark.asyncio
async def test_embed_raises_on_error():
    respx.post(OPENROUTER_EMBED_URL).mock(
        return_value=httpx.Response(500, text="Server Error")
    )
    client = EmbeddingsClient(api_key="test-key")
    with pytest.raises(RuntimeError, match="OpenRouter request failed"):
        await client.embed("hello")


def test_factory_returns_none_without_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    s = Settings(**BASE)  # no openrouter_api_key
    assert get_embeddings_client(s) is None


def test_factory_returns_client_with_key():
    s = Settings(**BASE, openrouter_api_key="sk-or-v1-test")
    client = get_embeddings_client(s)
    assert isinstance(client, EmbeddingsClient)
