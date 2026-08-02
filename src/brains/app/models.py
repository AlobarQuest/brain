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


def landing_check_constraints(table: str) -> tuple[CheckConstraint, ...]:
    """The two CHECKs that make a landing value legible and attributable.

    Shared by `apps` and `repositories` because the fact means the same thing on
    both while the move is in flight, and a constraint that exists on only one of
    two tables holding one fact is a gap, not a saving.
    """
    return (
        CheckConstraint(
            f"default_branch_landing IS NULL OR {landing_in_clause()}",
            name=f"ck_{table}_default_branch_landing",
        ),
        # The fact is not assertable without its provenance. A value with no
        # determination date and no evidence is an unattributable claim, and
        # knowing when and from what it was decided is the only thing that makes
        # a later drift check possible at all.
        CheckConstraint(
            "default_branch_landing IS NULL OR ("
            "default_branch_landing_determined_at IS NOT NULL "
            "AND btrim(default_branch_landing_evidence) <> '')",
            name=f"ck_{table}_default_branch_landing_provenance",
        ),
    )


# A repositories.canonical_slug is storable only in the one shape the read path
# looks up. Mirrors canonical_repo_slug's output; migration 0006 holds a frozen
# copy and tests/brains/test_repositories.py pins the two in sync.
CANONICAL_SLUG_PATTERN = r"^([A-Za-z0-9][A-Za-z0-9._-]*)/([A-Za-z0-9][A-Za-z0-9._-]*)(?:/|$)"


class Repository(Base):
    """A repository the estate reasons about — the subject of "does merging to
    the default branch change anything already running?".

    Distinct from an App because the factory can target a repository that
    deploys nothing (`intent-packages`, `project-standards`), and because one
    repository can feed several running apps (`AlobarQuest/brain` feeds four).
    The question is asked of the repository in both cases, so the answer lives
    here rather than being duplicated across, or missing from, the apps.

    A row may exist with no application: that IS the factory-targetable,
    not-deployed notion. Registering a repository is how the estate declares it
    a subject it will answer for; a repository with no row is `unknown`.
    """

    __tablename__ = "repositories"
    __table_args__ = (
        *landing_check_constraints("repositories"),
        CheckConstraint(
            "canonical_slug = lower(canonical_slug) "
            f"AND canonical_slug ~ '{CANONICAL_SLUG_PATTERN}'",
            name="ck_repositories_canonical_slug",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    # The lookup key: canonical_repo_slug() output, e.g. 'alobarquest/brain'.
    canonical_slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    # As the estate writes it, e.g. 'AlobarQuest/brain'. Display only — never
    # matched on, because the same repository is stored in several cases.
    github_repo: Mapped[str] = mapped_column(Text, nullable=False)
    default_branch_landing: Mapped[str | None] = mapped_column(Text, nullable=True)
    default_branch_landing_determined_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    default_branch_landing_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class App(Base):
    """A deployed application. An app is a DEPLOYMENT of a repository, joined to
    it by the canonical form of `github_repo`.

    The default-branch landing fact is deliberately NOT here: it belongs to the
    repository (see Repository), which one app may share with three siblings and
    which may exist with no app at all. Migration 0007 drops the columns 0005 put
    here; until then they are present in the database and read by nothing.
    """

    __tablename__ = "apps"

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
