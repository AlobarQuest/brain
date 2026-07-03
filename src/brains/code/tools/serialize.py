"""Shared dict serializers so MCP tools and REST endpoints return identical shapes."""


def road_dict(r) -> dict:
    return {
        "slug": r.slug,
        "name": r.name,
        "category": r.category,
        "status": r.status,
        "summary": r.summary,
        "decided_approach": r.decided_approach,
        "home": r.home,
        "owner_standard": r.owner_standard,
        "adr_ref": r.adr_ref,
        "last_validated_at": r.last_validated_at.isoformat() if r.last_validated_at else None,
        "validation_note": r.validation_note,
    }


def rule_dict(r) -> dict:
    return {
        "id": r.id,
        "road_slug": r.road_slug,
        "category": r.category,
        "severity": r.severity,
        "rule": r.rule,
        "reason": r.reason,
        "source": r.source,
        "check": r.check,
        "good_example": r.good_example,
        "bad_example": r.bad_example,
        "retired_at": r.retired_at.isoformat() if r.retired_at else None,
        "status": r.status,
        "authority": r.authority,
        "applicability": r.applicability,
        "conflict": r.conflict_kind,
    }


def lesson_dict(lesson) -> dict:
    return {
        "id": lesson.id,
        "road_slug": lesson.road_slug,
        "title": lesson.title,
        "content": lesson.content,
        "tags": lesson.tags or [],
        "source_app": lesson.source_app,
        "status": lesson.status,
        "authority": lesson.authority,
        "applicability": lesson.applicability,
        "conflict": lesson.conflict_kind,
    }


def exemplar_dict(e) -> dict:
    return {
        "id": e.id,
        "road_slug": e.road_slug,
        "label": e.label,
        "location": e.location,
        "note": e.note,
        "status": e.status,
        "authority": e.authority,
        "applicability": e.applicability,
        "conflict": e.conflict_kind,
    }
