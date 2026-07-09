from typing import Any

from src.brains.infra.models import Rule
from src.brains.infra.repositories.lessons import LessonRepository
from src.brains.infra.repositories.rules import RuleRepository
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
    app: str | None = None,
    tags: list[str] | None = None,
    severity: str = "INFO",
    proposed_by: str | None = None,
    authority: str = "informational",
) -> tuple[Any | None, dict | None]:
    if severity not in ("CRITICAL", "WARN", "INFO"):
        return None, {"error": "invalid_severity", "allowed": ["CRITICAL", "WARN", "INFO"]}
    if authority not in VALID_AUTHORITIES:
        return None, {"error": "invalid_authority", "allowed": list(VALID_AUTHORITIES)}
    data = {
        "title": title,
        "content": content,
        "app": app,
        "tags": tags or [],
        "severity": severity,
    }
    data.update(
        proposed_defaults(
            proposed_by=proposed_by,
            applicability={"app": app},
            auto_approve=False,
        )
    )
    data["authority"] = authority
    lesson = await LessonRepository(session).add(data)
    return lesson, None


async def propose_rule(
    session,
    *,
    severity: str,
    category: str,
    rule: str,
    reason: str,
    source_app: str | None = None,
    check: dict | None = None,
    proposed_by: str | None = None,
    authority: str = "informational",
) -> tuple[Any | None, dict | None]:
    if severity not in ("BLOCK", "WARN", "INFO"):
        return None, {"error": "invalid_severity", "allowed": ["BLOCK", "WARN", "INFO"]}
    if authority not in VALID_AUTHORITIES:
        return None, {"error": "invalid_authority", "allowed": list(VALID_AUTHORITIES)}
    applicability = {"category": category, "source_app": source_app}
    data = {
        "severity": severity,
        "category": category,
        "rule": rule,
        "reason": reason,
        "source_app": source_app,
        "check": check,
    }
    data.update(
        proposed_defaults(
            proposed_by=proposed_by,
            applicability=applicability,
            auto_approve=False,
        )
    )
    data["authority"] = authority
    flag = await find_conflicts(
        session,
        Rule,
        candidate_check=check,
        overlap_key_fields=("category", "source_app"),
        candidate=applicability,
    )
    finalize_governance(data, flag)
    proposed = await RuleRepository(session).add(data)
    return proposed, None
