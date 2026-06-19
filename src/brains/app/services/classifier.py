import httpx

from src.core.config import get_settings

KNOWLEDGE_TYPES = [
    "architecture", "business", "intent", "requirements",
    "api", "data_model", "deployment", "status", "feature", "rules",
]

HINT_FALLBACKS = {
    "readme": "architecture",
    "charter": "intent",
    "architecture_notes": "architecture",
    "deployment_notes": "deployment",
}

TYPES_LIST = ", ".join(KNOWLEDGE_TYPES)


async def classify_chunk(content: str, hint: str) -> str:
    raw = await _classify_via_llm(content, hint)
    if raw in KNOWLEDGE_TYPES:
        return raw
    return HINT_FALLBACKS.get(hint, "architecture")


async def _classify_via_llm(content: str, hint: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {get_settings().openrouter_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "openai/gpt-4o-mini",
                "messages": [{
                    "role": "user",
                    "content": (
                        f"Classify this knowledge chunk into exactly one of these types: {TYPES_LIST}\n\n"
                        f'The chunk comes from a "{hint}" document.\n'
                        f"Reply with just the type name, nothing else.\n\n"
                        f"Chunk:\n{content[:500]}"
                    ),
                }],
            },
        )
        if not r.is_success:
            raise RuntimeError(f"Classifier LLM failed: {r.status_code}")
        data = r.json()
        return data["choices"][0]["message"]["content"].strip().lower()
