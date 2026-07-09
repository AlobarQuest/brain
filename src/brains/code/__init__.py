"""Code Brain — the machine source of record for portfolio-wide code patterns.

The 4th brain (BRAIN_TYPE=code), modeled directly on src/brains/infra. No pgvector
for v1 (embeddings=False): structured roads/rules + keyword search.
"""

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from src.brains.code.models import Exemplar, Lesson, Rule
from src.brains.code.repositories.exemplars import ExemplarRepository
from src.brains.code.repositories.lessons import LessonRepository
from src.brains.code.repositories.roads import RoadRepository
from src.brains.code.repositories.rules import RuleRepository
from src.brains.code.repositories.search import SearchRepository
from src.brains.code.services.proposals import propose_lesson, propose_rule
from src.brains.code.tools.exemplars import register_exemplar_tools
from src.brains.code.tools.lessons import register_lesson_tools
from src.brains.code.tools.roads import register_road_tools
from src.brains.code.tools.rules import register_rule_tools
from src.brains.code.tools.search import register_search_tools
from src.brains.code.tools.serialize import exemplar_dict, lesson_dict, road_dict, rule_dict
from src.core.db import get_session_factory
from src.core.governance import register_governance_tools
from src.core.registry import Capabilities

capabilities = Capabilities(embeddings=False, auth_exact=("/api/health",), auth_prefixes=())


def register(mcp) -> None:
    register_road_tools(mcp)
    register_rule_tools(mcp)
    register_lesson_tools(mcp)
    register_exemplar_tools(mcp)
    register_search_tools(mcp)
    register_governance_tools(mcp, {"rule": Rule, "lesson": Lesson, "exemplar": Exemplar})


def _road_rest(r) -> dict:
    d = road_dict(r)
    d["created_at"] = r.created_at.isoformat()
    d["updated_at"] = r.updated_at.isoformat()
    return d


def _rule_rest(r) -> dict:
    d = rule_dict(r)
    d["created_at"] = r.created_at.isoformat()
    return d


class CodeLessonProposal(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=4000)
    road_slug: str | None = Field(default=None, max_length=120)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_app: str | None = Field(default=None, max_length=120)
    proposed_by: str | None = Field(default=None, max_length=120)
    authority: str = Field(default="informational")


class CodeRuleProposal(BaseModel):
    road_slug: str = Field(min_length=1, max_length=120)
    severity: str = Field(min_length=1, max_length=20)
    category: str = Field(min_length=1, max_length=80)
    rule: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    source: str | None = Field(default=None, max_length=200)
    check: dict[str, Any] | None = None
    good_example: str | None = Field(default=None, max_length=2000)
    bad_example: str | None = Field(default=None, max_length=2000)
    proposed_by: str | None = Field(default=None, max_length=120)
    authority: str = Field(default="informational")


def register_routes(app) -> None:
    """REST lookup API beyond /api/health (auth-protected by the core middleware).

    Lets off-machine agents query Code Brain accurately without an MCP client.
    Mirrors infra brain's GET /api/rules (REST includes created_at; the MCP tools
    omit it). Write-back stays on MCP, exactly like the other brains.
    """

    @app.get("/api/roads")
    async def list_roads_api(
        category: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ):
        limit = max(1, min(limit, 200))
        async with get_session_factory()() as session:
            roads = await RoadRepository(session).list_all(
                category=category, status=status, limit=limit
            )
            return {"roads": [_road_rest(r) for r in roads]}

    @app.get("/api/road/{slug}")
    async def get_road_api(slug: str):
        async with get_session_factory()() as session:
            road = await RoadRepository(session).get_by_slug(slug)
            if road is None:
                raise HTTPException(status_code=404, detail=f"unknown road: {slug}")
            rules = await RuleRepository(session).list_all(road_slug=slug)
            exemplars = await ExemplarRepository(session).list_for_road(slug)
            lessons = await LessonRepository(session).list_for_road(slug)
            return {
                "road": _road_rest(road),
                "rules": [_rule_rest(r) for r in rules],
                "exemplars": [exemplar_dict(e) for e in exemplars],
                "lessons": [lesson_dict(lesson) for lesson in lessons],
            }

    @app.get("/api/rules")
    async def list_rules_api(
        category: str | None = None,
        severity: str | None = None,
        road_slug: str | None = None,
        include_retired: bool = False,
    ):
        async with get_session_factory()() as session:
            rules = await RuleRepository(session).list_all(
                category=category,
                severity=severity,
                road_slug=road_slug,
                include_retired=include_retired,
            )
            return {"rules": [_rule_rest(r) for r in rules]}

    @app.get("/api/search")
    async def search_api(query: str, limit: int = 50):
        limit = max(1, min(limit, 50))
        async with get_session_factory()() as session:
            results = await SearchRepository(session).search(query=query, limit=limit)
            return {
                "roads": [_road_rest(r) for r in results["roads"]],
                "rules": [_rule_rest(r) for r in results["rules"]],
                "lessons": [lesson_dict(lesson) for lesson in results["lessons"]],
            }

    @app.post("/api/proposals/lessons", status_code=201)
    async def propose_lesson_api(body: CodeLessonProposal):
        async with get_session_factory()() as session:
            lesson, error = await propose_lesson(session, **body.model_dump())
            if error is not None:
                raise HTTPException(status_code=400, detail=error)
            await session.commit()
            return {"created": True, "lesson": lesson_dict(lesson)}

    @app.post("/api/proposals/rules", status_code=201)
    async def propose_rule_api(body: CodeRuleProposal):
        async with get_session_factory()() as session:
            rule, error = await propose_rule(session, **body.model_dump())
            if error is not None:
                raise HTTPException(status_code=400, detail=error)
            await session.commit()
            return {"created": True, "rule": rule_dict(rule)}
