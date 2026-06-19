from src.core.registry import Capabilities
from src.brains.app.tools.apps import register_app_tools
from src.brains.app.tools.knowledge import register_knowledge_tools

capabilities = Capabilities(embeddings=True, auth_allowlist=("/api/health", "/register", "/.well-known"))


def register(mcp) -> None:
    register_app_tools(mcp)
    register_knowledge_tools(mcp)
