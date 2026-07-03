from src.core.registry import Capabilities
from src.brains.open.tools.thoughts import register_thought_tools

capabilities = Capabilities(embeddings=True, auth_exact=("/api/health",), auth_prefixes=())


def register(mcp) -> None:
    register_thought_tools(mcp)
    # No register_governance_tools call (Sub-B): thoughts have no in-place approve/
    # reject/deprecate gate — every thought lands pre-approved; promotion into a
    # knowledge brain is WS-6.2, out of scope here.
