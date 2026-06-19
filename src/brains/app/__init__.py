from src.core.registry import Capabilities
from src.brains.app.tools.apps import register_app_tools
from src.brains.app.tools.knowledge import register_knowledge_tools

capabilities = Capabilities(
    embeddings=True,
    auth_exact=("/api/health", "/register"),
    auth_prefixes=("/.well-known/",),
)


def register(mcp) -> None:
    register_app_tools(mcp)
    register_knowledge_tools(mcp)


async def startup() -> None:
    from src.core.db import get_session_factory
    from src.brains.app.repositories.apps import AppRepository
    async with get_session_factory()() as session:
        await AppRepository(session).fail_stale_running()
        await session.commit()
