from src.core.registry import Capabilities
from src.brains.open.tools.thoughts import register_thought_tools

capabilities = Capabilities(embeddings=True, auth_exact=("/api/health",), auth_prefixes=())


def register(mcp) -> None:
    register_thought_tools(mcp)
