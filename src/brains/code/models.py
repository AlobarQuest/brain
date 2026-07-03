"""Code Brain ORM models — the machine source of record for portfolio-wide code patterns.

Modeled on src/brains/infra/models.py. Two core tables (Road, Rule) + two light
ones (Lesson, Exemplar).

NOTE: the code brain declares its OWN ``Base`` (declarative metadata) rather than
reusing ``src.core.db.Base``. Its ``rules`` and ``lessons`` table names collide
with infra brain's same-named tables, and a shared MetaData rejects duplicate
table names the moment both modules are imported in one process (e.g. the test
suite). In production only one brain is imported per process, so the runtime
engine/session — which are metadata-agnostic — work identically either way.
"""
from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.core.governance import GovernanceMixin, governance_check_constraints


class Base(DeclarativeBase):
    pass


_CATEGORIES = (
    "'application', 'data', 'api', 'frontend', 'delivery-ops', 'quality', "
    "'security', 'ai'"
)
_STATUSES = "'paved', 'partial', 'unpaved', 'paving'"


class Road(Base):
    """The paved-road catalog — one row per cross-cutting concern."""

    __tablename__ = "roads"
    __table_args__ = (
        CheckConstraint(f"category IN ({_CATEGORIES})", name="roads_category_check"),
        CheckConstraint(f"status IN ({_STATUSES})", name="roads_status_check"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    decided_approach: Mapped[str | None] = mapped_column(Text, nullable=True)
    home: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_standard: Mapped[str | None] = mapped_column(Text, nullable=True)
    adr_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_validated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    validation_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class Rule(Base, GovernanceMixin):
    """A normative statement within a road (mirrors infra brain's rules)."""

    __tablename__ = "rules"
    __table_args__ = (
        CheckConstraint("severity IN ('BLOCK', 'WARN', 'INFO')", name="rules_severity_check"),
        *governance_check_constraints("rules"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    road_slug: Mapped[str] = mapped_column(ForeignKey("roads.slug"), nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    check: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    good_example: Mapped[str | None] = mapped_column(Text, nullable=True)
    bad_example: Mapped[str | None] = mapped_column(Text, nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"), nullable=True)


class Lesson(Base, GovernanceMixin):
    """A lesson learned within a road (road_slug null = general)."""

    __tablename__ = "lessons"
    __table_args__ = governance_check_constraints("lessons")

    id: Mapped[int] = mapped_column(primary_key=True)
    road_slug: Mapped[str | None] = mapped_column(ForeignKey("roads.slug"), nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True, default=list)
    source_app: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id"), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id"), nullable=True)


class Exemplar(Base, GovernanceMixin):
    """A canonical example implementation of a road."""

    __tablename__ = "exemplars"
    __table_args__ = governance_check_constraints("exemplars")

    id: Mapped[int] = mapped_column(primary_key=True)
    road_slug: Mapped[str] = mapped_column(ForeignKey("roads.slug"), nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    location: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("exemplars.id"), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("exemplars.id"), nullable=True
    )
