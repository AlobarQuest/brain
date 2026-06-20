from src.core.registry import Capabilities
from src.core.db import get_session_factory
from src.brains.infra.repositories.rules import RuleRepository
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


def register_routes(app) -> None:
    """REST endpoints beyond /api/health (auth-protected by the core middleware).

    Ported from the original infra-brain ``GET /api/rules``. Consumed by the
    infraops standards audit (which sends the x-brain-key), so its shape must
    stay stable — note it includes ``created_at`` (the MCP get_rules tool omits it).
    """

    @app.get("/api/rules")
    async def list_rules_api(
        category: str | None = None,
        severity: str | None = None,
        include_retired: bool = False,
    ):
        async with get_session_factory()() as session:
            repo = RuleRepository(session)
            rules = await repo.list_all(
                category=category, severity=severity, include_retired=include_retired
            )
            return {
                "rules": [
                    {
                        "id": r.id,
                        "severity": r.severity,
                        "category": r.category,
                        "rule": r.rule,
                        "reason": r.reason,
                        "source_app": r.source_app,
                        "check": r.check,
                        "retired_at": r.retired_at.isoformat() if r.retired_at else None,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in rules
                ]
            }
