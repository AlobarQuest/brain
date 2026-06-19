"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-24

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # Create thoughts table (embedding added via raw SQL since Alembic
    # doesn't natively support pgvector types)
    op.create_table(
        "thoughts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    # Add vector column via raw SQL
    op.execute("ALTER TABLE thoughts ADD COLUMN embedding vector(1536)")

    # Indexes
    op.execute(
        "CREATE INDEX thoughts_embedding_idx ON thoughts "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX thoughts_metadata_idx ON thoughts "
        "USING gin (metadata)"
    )
    op.create_index("thoughts_created_at_idx", "thoughts", [sa.text("created_at DESC")])

    # updated_at trigger
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
        CREATE TRIGGER thoughts_updated_at
            BEFORE UPDATE ON thoughts
            FOR EACH ROW
            EXECUTE FUNCTION update_updated_at()
    """)

    # Semantic search function
    op.execute("""
        CREATE OR REPLACE FUNCTION match_thoughts(
            query_embedding vector(1536),
            match_threshold float DEFAULT 0.7,
            match_count int DEFAULT 10,
            filter jsonb DEFAULT '{}'::jsonb
        )
        RETURNS TABLE (
            id uuid,
            content text,
            metadata jsonb,
            similarity float,
            created_at timestamptz
        )
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RETURN QUERY
            SELECT
                t.id,
                t.content,
                t.metadata,
                (1 - (t.embedding <=> query_embedding))::float AS similarity,
                t.created_at
            FROM thoughts t
            WHERE 1 - (t.embedding <=> query_embedding) > match_threshold
            AND (filter = '{}'::jsonb OR t.metadata @> filter)
            ORDER BY t.embedding <=> query_embedding
            LIMIT match_count;
        END;
        $$
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS match_thoughts")
    op.execute("DROP TRIGGER IF EXISTS thoughts_updated_at ON thoughts")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at")
    op.drop_table("thoughts")
    op.execute("DROP EXTENSION IF EXISTS vector")
