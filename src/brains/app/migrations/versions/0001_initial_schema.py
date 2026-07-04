"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-27

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- apps table ---
    op.create_table(
        "apps",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("slug", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("tech_stack", postgresql.JSONB(), nullable=True),
        sa.Column("repo_path", sa.Text(), nullable=True),
        sa.Column("deployment_url", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "onboarding_status", sa.Text(), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("last_onboarded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_onboarding_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # --- app_knowledge table ---
    op.create_table(
        "app_knowledge",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "app_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("apps.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("app_slug", sa.Text(), nullable=False),
        sa.Column("knowledge_type", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("source", sa.Text(), nullable=False, server_default=sa.text("'mcp'")),
        sa.Column(
            "supersedes_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("app_knowledge.id"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )

    # Add vector column via raw SQL
    op.execute("ALTER TABLE app_knowledge ADD COLUMN embedding vector(1536)")

    # --- Indexes ---

    # Vector similarity search (HNSW) on active chunks only
    op.execute(
        "CREATE INDEX app_knowledge_embedding_idx ON app_knowledge "
        "USING hnsw (embedding vector_cosine_ops) WHERE is_active = true"
    )

    # Full-text search (GIN)
    op.execute(
        "CREATE INDEX app_knowledge_fts_idx ON app_knowledge "
        "USING gin (to_tsvector('simple', content)) WHERE is_active = true"
    )

    # Metadata JSONB GIN
    op.execute("CREATE INDEX app_knowledge_metadata_idx ON app_knowledge USING gin (metadata)")

    # Deduplication unique constraint
    op.execute(
        "CREATE UNIQUE INDEX app_knowledge_dedup_idx ON app_knowledge "
        "(app_slug, knowledge_type, content_hash) WHERE is_active = true"
    )

    # Recency
    op.create_index("app_knowledge_created_at_idx", "app_knowledge", [sa.text("created_at DESC")])

    # Scoped queries
    op.execute(
        "CREATE INDEX app_knowledge_slug_type_idx ON app_knowledge "
        "(app_slug, knowledge_type) WHERE is_active = true"
    )

    # --- Triggers ---

    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at()
        RETURNS trigger AS $$
        BEGIN
            NEW.updated_at = now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER apps_updated_at
            BEFORE UPDATE ON apps
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at()
    """)

    op.execute("""
        CREATE TRIGGER app_knowledge_updated_at
            BEFORE UPDATE ON app_knowledge
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at()
    """)

    # --- Semantic search function ---

    op.execute("""
        CREATE OR REPLACE FUNCTION match_app_knowledge_semantic(
            query_embedding vector(1536),
            match_threshold float DEFAULT 0.5,
            match_count int DEFAULT 10,
            app_slug_filter text DEFAULT NULL,
            type_filter text DEFAULT NULL
        )
        RETURNS TABLE (
            id uuid,
            app_slug text,
            knowledge_type text,
            content text,
            metadata jsonb,
            similarity float
        )
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RETURN QUERY
            SELECT
                k.id,
                k.app_slug,
                k.knowledge_type,
                k.content,
                k.metadata,
                (1 - (k.embedding <=> query_embedding))::float AS similarity
            FROM app_knowledge k
            WHERE k.is_active = true
            AND 1 - (k.embedding <=> query_embedding) > match_threshold
            AND (app_slug_filter IS NULL OR k.app_slug = app_slug_filter)
            AND (type_filter IS NULL OR k.knowledge_type = type_filter)
            ORDER BY k.embedding <=> query_embedding
            LIMIT match_count;
        END;
        $$
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS match_app_knowledge_semantic")
    op.execute("DROP TRIGGER IF EXISTS app_knowledge_updated_at ON app_knowledge")
    op.execute("DROP TRIGGER IF EXISTS apps_updated_at ON apps")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at")
    op.drop_table("app_knowledge")
    op.drop_table("apps")
    op.execute("DROP EXTENSION IF EXISTS vector")
