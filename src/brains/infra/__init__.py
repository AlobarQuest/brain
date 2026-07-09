from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from src.brains.infra.models import Combo, Lesson, Rule
from src.brains.infra.repositories.rules import RuleRepository
from src.brains.infra.services.proposals import propose_lesson, propose_rule
from src.brains.infra.tools.combos import register_combo_tools
from src.brains.infra.tools.lessons import register_lesson_tools
from src.brains.infra.tools.rules import register_rule_tools
from src.brains.infra.tools.versions import register_version_tools
from src.core.db import get_session_factory
from src.core.governance import register_governance_tools
from src.core.registry import Capabilities

capabilities = Capabilities(embeddings=False, auth_exact=("/api/health",), auth_prefixes=())


def register(mcp) -> None:
    register_version_tools(mcp)
    register_rule_tools(mcp)
    register_combo_tools(mcp)
    register_lesson_tools(mcp)
    register_governance_tools(mcp, {"rule": Rule, "lesson": Lesson, "combo": Combo})


class InfraLessonProposal(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=4000)
    app: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)
    severity: str = Field(default="INFO", max_length=20)
    proposed_by: str | None = Field(default=None, max_length=120)
    authority: str = Field(default="informational")


class InfraRuleProposal(BaseModel):
    severity: str = Field(min_length=1, max_length=20)
    category: str = Field(min_length=1, max_length=80)
    rule: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    source_app: str | None = Field(default=None, max_length=120)
    check: dict[str, Any] | None = None
    proposed_by: str | None = Field(default=None, max_length=120)
    authority: str = Field(default="informational")


def _lesson_rest(lesson) -> dict:
    return {
        "id": lesson.id,
        "app": lesson.app,
        "title": lesson.title,
        "content": lesson.content,
        "tags": lesson.tags or [],
        "severity": lesson.severity,
        "source": lesson.source,
        "status": lesson.status,
        "authority": lesson.authority,
        "applicability": lesson.applicability,
        "conflict": lesson.conflict_kind,
    }


def _rule_rest(rule) -> dict:
    return {
        "id": rule.id,
        "severity": rule.severity,
        "category": rule.category,
        "rule": rule.rule,
        "reason": rule.reason,
        "source_app": rule.source_app,
        "check": rule.check,
        "retired_at": rule.retired_at.isoformat() if rule.retired_at else None,
        "created_at": rule.created_at.isoformat(),
        "status": rule.status,
        "authority": rule.authority,
        "applicability": rule.applicability,
        "conflict": rule.conflict_kind,
    }


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
            return {"rules": [_rule_rest(r) for r in rules]}

    @app.post("/api/proposals/lessons", status_code=201)
    async def propose_lesson_api(body: InfraLessonProposal):
        async with get_session_factory()() as session:
            lesson, error = await propose_lesson(session, **body.model_dump())
            if error is not None:
                raise HTTPException(status_code=400, detail=error)
            await session.commit()
            return {"created": True, "lesson": _lesson_rest(lesson)}

    @app.post("/api/proposals/rules", status_code=201)
    async def propose_rule_api(body: InfraRuleProposal):
        async with get_session_factory()() as session:
            rule, error = await propose_rule(session, **body.model_dump())
            if error is not None:
                raise HTTPException(status_code=400, detail=error)
            await session.commit()
            return {"created": True, "rule": _rule_rest(rule)}
