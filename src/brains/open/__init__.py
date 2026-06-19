from src.core.registry import Capabilities
from src.brains.open.tools.thoughts import register_thought_tools

capabilities = Capabilities(embeddings=True, auth_allowlist=("/api/health",))


def register(mcp) -> None:
    register_thought_tools(mcp)
