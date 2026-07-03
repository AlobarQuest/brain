from datetime import datetime

from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.governance import GovernanceMixin, governance_check_constraints


class Version(Base):
    __tablename__ = "versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    package: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    canonical: Mapped[str] = mapped_column(Text, nullable=False)
    min_allowed: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocked_above: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_in: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True, default=list)
    ecosystem: Mapped[str] = mapped_column(Text, nullable=False, server_default="python")
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    updated_by: Mapped[str] = mapped_column(Text, nullable=False, server_default="ai-capture")


class Rule(Base, GovernanceMixin):
    __tablename__ = "rules"
    __table_args__ = (
        CheckConstraint("severity IN ('BLOCK', 'WARN', 'INFO')", name="rules_severity_check"),
        *governance_check_constraints("rules"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    rule: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source_app: Mapped[str | None] = mapped_column(Text, nullable=True)
    check: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("rules.id"), nullable=True)


class Combo(Base, GovernanceMixin):
    __tablename__ = "combos"
    __table_args__ = governance_check_constraints("combos")

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    packages: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ecosystem: Mapped[str] = mapped_column(Text, nullable=False)
    flavor: Mapped[str | None] = mapped_column(Text, nullable=True)
    confirmed_in: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("combos.id"), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("combos.id"), nullable=True)


class Lesson(Base, GovernanceMixin):
    __tablename__ = "lessons"
    __table_args__ = (
        CheckConstraint("severity IN ('CRITICAL', 'WARN', 'INFO')", name="lessons_severity_check"),
        *governance_check_constraints("lessons"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    app: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True, default=list)
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default="INFO")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="ai-capture")
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id"), nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(ForeignKey("lessons.id"), nullable=True)
