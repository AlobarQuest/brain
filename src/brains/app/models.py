import uuid
from datetime import datetime

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import TIMESTAMP, CheckConstraint, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.db import Base
from src.core.governance import GovernanceMixin, governance_check_constraints

# --- default-branch landing vocabulary ------------------------------------
# "Does landing a commit on this app's default branch change something that is
# already running?" Closed, two-valued, and deliberately WITHOUT a stored
# "unknown": an app nobody has assessed holds NULL, so unknown is the state a row
# is born in and cannot be forged into an answer. The read path projects NULL to
# the served string below.
# redeploys: merging advances something already serving, with no further human act.
# inert:     nothing already serving changes until a separate act.
LANDING_REDEPLOYS = "redeploys"
LANDING_INERT = "inert"
LANDING_VALUES = (LANDING_REDEPLOYS, LANDING_INERT)
LANDING_UNKNOWN = "unknown"  # served only; never stored


def landing_in_clause() -> str:
    """SQL membership test for the closed vocabulary.

    Built by joining rather than by `f"... IN {LANDING_VALUES!r}"`: a one-element
    tuple's repr carries a trailing comma, which Postgres rejects as a syntax
    error. Two members today; this construction survives losing one.
    """
    values = ", ".join(f"'{v}'" for v in LANDING_VALUES)
    return f"default_branch_landing IN ({values})"


class App(Base):
    __tablename__ = "apps"
    __table_args__ = (
        CheckConstraint(
            f"default_branch_landing IS NULL OR {landing_in_clause()}",
            name="ck_apps_default_branch_landing",
        ),
        # The fact is not assertable without its provenance. A value with no
        # determination date and no evidence is an unattributable claim, and
        # knowing when and from what it was decided is the only thing that makes
        # a later drift check possible at all.
        CheckConstraint(
            "default_branch_landing IS NULL OR ("
            "default_branch_landing_determined_at IS NOT NULL "
            "AND default_branch_landing_evidence IS NOT NULL)",
            name="ck_apps_default_branch_landing_provenance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tech_stack: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    repo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployment_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    github_repo: Mapped[str | None] = mapped_column(Text, nullable=True)
    environments: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    # Deliberately NOT a key inside environments[]: that array is
    # Coolify-authoritative and is overwritten wholesale by
    # scripts/sync_deployment_from_coolify.py. The trigger this answers lives in
    # the source repository (or in a git-provider webhook) and points AT the
    # deploy target, so the deploy target structurally cannot see it — a value
    # stored there would be erased by the very blindness it exists to fix.
    default_branch_landing: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_branch_landing_determined_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    default_branch_landing_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("'active'"))
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sa.text("'{}'::text[]")
    )
    onboarding_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=sa.text("'pending'")
    )
    last_onboarded_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    last_onboarding_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class AppKnowledge(Base, GovernanceMixin):
    __tablename__ = "app_knowledge"
    __table_args__ = governance_check_constraints("app_knowledge")

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    app_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("apps.id", ondelete="CASCADE"), nullable=False
    )
    app_slug: Mapped[str] = mapped_column(Text, nullable=False)
    knowledge_type: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(1536), nullable=True)
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=sa.text("'mcp'"))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_knowledge.id"), nullable=True
    )
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_knowledge.id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(nullable=False, server_default=sa.text("true"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
