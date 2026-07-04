"""add check constraints for enum-like fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-03-27

"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE apps ADD CONSTRAINT chk_apps_status
        CHECK (status IN ('active', 'archived', 'in-progress', 'paused'))
    """)
    op.execute("""
        ALTER TABLE apps ADD CONSTRAINT chk_apps_onboarding_status
        CHECK (onboarding_status IN ('pending', 'running', 'complete', 'partial', 'failed'))
    """)
    op.execute("""
        ALTER TABLE app_knowledge ADD CONSTRAINT chk_knowledge_type
        CHECK (knowledge_type IN (
            'architecture', 'business', 'intent', 'requirements',
            'api', 'data_model', 'deployment', 'status', 'feature', 'rules'
        ))
    """)
    op.execute("""
        ALTER TABLE app_knowledge ADD CONSTRAINT chk_knowledge_source
        CHECK (source IN ('onboard', 'mcp', 'ai-capture', 'manual'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE app_knowledge DROP CONSTRAINT IF EXISTS chk_knowledge_source")
    op.execute("ALTER TABLE app_knowledge DROP CONSTRAINT IF EXISTS chk_knowledge_type")
    op.execute("ALTER TABLE apps DROP CONSTRAINT IF EXISTS chk_apps_onboarding_status")
    op.execute("ALTER TABLE apps DROP CONSTRAINT IF EXISTS chk_apps_status")
