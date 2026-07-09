from typing import Any

from src.brains.code.models import Rule
from src.brains.code.repositories.lessons import LessonRepository
from src.brains.code.repositories.roads import RoadRepository
from src.brains.code.repositories.rules import RuleRepository
from src.core.governance import (
    VALID_AUTHORITIES,
    finalize_governance,
    find_conflicts,
    proposed_defaults,
)


async def propose_lesson(
    session,
    *,
    title: str,
    content: str,
    road_slug: str | None = None,
    tags: list[str] | None = None,
    source_app: str | None = None,
    proposed_by: str | None = None,
    authority: str = "informational",
) -> tuple[Any | None, dict | None]:
    if authority not in VALID_AUTHORITIES:
        return None, {"error": "invalid_authority", "allowed": list(VALID_AUTHORITIES)}
    if road_slug is not None and await RoadRepository(session).get_by_slug(road_slug) is None:
        return None, {"error": "unknown_road", "road_slug": road_slug}
    data = {
        "title": title,
        "content": content,
        "road_slug": road_slug,
        "tags": tags or [],
        "source_app": source_app,
    }
    data.update(
        proposed_defaults(
            proposed_by=proposed_by,
            applicability={"road_slug": road_slug},
            auto_approve=False,
        )
    )
    data["authority"] = authority
    lesson = await LessonRepository(session).add(data)
    return lesson, None


async def propose_rule(
    session,
    *,
    road_slug: str,
    severity: str,
    category: str,
    rule: str,
    reason: str,
    source: str | None = None,
    check: dict | None = None,
    good_example: str | None = None,
    bad_example: str | None = None,
    proposed_by: str | None = None,
    authority: str = "informational",
) -> tuple[Any | None, dict | None]:
    if severity not in ("BLOCK", "WARN", "INFO"):
        return None, {"error": "invalid_severity", "allowed": ["BLOCK", "WARN", "INFO"]}
    if authority not in VALID_AUTHORITIES:
        return None, {"error": "invalid_authority", "allowed": list(VALID_AUTHORITIES)}
    if await RoadRepository(session).get_by_slug(road_slug) is None:
        return None, {"error": "unknown_road", "road_slug": road_slug}
    applicability = {"road_slug": road_slug, "category": category}
    data = {
        "road_slug": road_slug,
        "severity": severity,
        "category": category,
        "rule": rule,
        "reason": reason,
        "source": source,
        "check": check,
        "good_example": good_example,
        "bad_example": bad_example,
    }
    data.update(
        proposed_defaults(
            proposed_by=proposed_by,
            applicability=applicability,
            auto_approve=False,
        )
    )
    data["authority"] = authority
    candidate_check = check if (check and check.get("kind") != "judgment") else None
    flag = await find_conflicts(
        session,
        Rule,
        candidate_check=candidate_check,
        overlap_key_fields=("road_slug", "category"),
        candidate=applicability,
    )
    finalize_governance(data, flag)
    proposed = await RuleRepository(session).add(data)
    return proposed, None
