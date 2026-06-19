from src.core.registry import Capabilities
from src.brains.infra.tools.rules import register_rule_tools
from src.brains.infra.tools.combos import register_combo_tools
from src.brains.infra.tools.lessons import register_lesson_tools
from src.brains.infra.tools.versions import register_version_tools

capabilities = Capabilities(embeddings=False, auth_exact=("/api/health",), auth_prefixes=())


def register(mcp) -> None:
    register_version_tools(mcp)
    register_rule_tools(mcp)
    register_combo_tools(mcp)
    register_lesson_tools(mcp)
