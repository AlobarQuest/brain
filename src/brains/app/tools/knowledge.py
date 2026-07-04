import asyncio
import logging
import uuid
from typing import Optional

from fastmcp import FastMCP

from src.brains.app.models import AppKnowledge
from src.brains.app.repositories.apps import AppRepository
from src.brains.app.repositories.knowledge import KnowledgeRepository
from src.brains.app.services.chunker import chunk_text
from src.brains.app.services.classifier import KNOWLEDGE_TYPES
from src.brains.app.services.hash import compute_content_hash
from src.brains.app.services.onboarding import run_onboarding_job
from src.brains.app.services.openrouter import embed, extract_metadata
from src.core.db import get_session_factory
from src.core.governance import (
    finalize_governance,
    find_conflicts,
    proposed_defaults,
    require_approver,
)

logger = logging.getLogger("app_brain.tools")

SEARCH_MODES = ["hybrid", "semantic", "keyword"]
SOURCE_VALUES = ["onboard", "mcp", "ai-capture", "manual"]
ONBOARDING_BLOB_FIELDS = ["readme", "charter", "architecture_notes", "deployment_notes"]
APP_STATUSES = ["active", "archived", "in-progress", "paused"]

# Hold references to scheduled background tasks so they aren't garbage-collected mid-run.
_background_tasks: set = set()


def register_knowledge_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def search_knowledge(
        query: str,
        app_slug: Optional[str] = None,
        knowledge_type: Optional[str] = None,
        mode: str = "hybrid",
        limit: int = 10,
        threshold: float = 0.5,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> dict:
        """Search knowledge chunks by meaning (semantic), keywords, or both (hybrid). Use to find
        information about apps in the brain. Non-approved (proposed/deprecated/superseded)
        knowledge is excluded unless include_proposed is set. min_authority filters to
        authority >= the given rank (informational < recommended < required)."""
        if mode not in SEARCH_MODES:
            return {"error": f"invalid_params: mode must be one of {', '.join(SEARCH_MODES)}"}
        limit = min(limit, 50)

        async with get_session_factory()() as session:
            repo = KnowledgeRepository(session)
            semantic_results: list[dict] = []
            fts_results: list[dict] = []

            if mode in ("semantic", "hybrid"):
                query_embedding = await embed(query)
                semantic_results = await repo.search_semantic(
                    query_embedding=query_embedding,
                    threshold=threshold,
                    limit=limit,
                    app_slug=app_slug,
                    knowledge_type=knowledge_type,
                    include_proposed=include_proposed,
                    min_authority=min_authority,
                )

            if mode in ("keyword", "hybrid"):
                fts_results = await repo.search_keyword(
                    query=query,
                    limit=limit,
                    app_slug=app_slug,
                    knowledge_type=knowledge_type,
                    include_proposed=include_proposed,
                    min_authority=min_authority,
                )

        if mode == "semantic":
            return {"results": semantic_results}
        if mode == "keyword":
            return {"results": fts_results}

        # Hybrid: merge, semantic first, dedupe
        seen = set()
        merged = []
        for row in semantic_results:
            seen.add(row["id"])
            merged.append(row)
        for row in fts_results:
            if row["id"] not in seen:
                merged.append(row)
        return {"results": merged}

    @mcp.tool()
    async def list_knowledge(
        app_slug: str,
        knowledge_type: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        active_only: bool = True,
        include_proposed: bool = False,
        min_authority: str | None = None,
    ) -> dict:
        """Browse recent knowledge chunks for an app with optional filters. active_only (default
        True) excludes inactive chunks; non-approved (proposed/deprecated/superseded) knowledge
        is additionally excluded unless include_proposed is set. min_authority filters to
        authority >= the given rank (informational < recommended < required)."""
        limit = min(limit, 100)
        async with get_session_factory()() as session:
            repo = KnowledgeRepository(session)
            chunks = await repo.list_knowledge(
                app_slug=app_slug,
                knowledge_type=knowledge_type,
                limit=limit,
                offset=offset,
                active_only=active_only,
                include_proposed=include_proposed,
                min_authority=min_authority,
            )
        return {"chunks": chunks}

    @mcp.tool()
    async def capture_knowledge(
        app_slug: str,
        knowledge_type: str,
        content: str,
        source: str = "mcp",
        supersedes_id: Optional[str] = None,
        proposed_by: str | None = None,
        auto_approve: bool = False,
    ) -> dict:
        """Store a new knowledge chunk for an app (status=proposed by default; approved only
        with the approver key and auto_approve=True). Auto-generates embedding and extracts
        metadata. A candidate whose (app_slug, knowledge_type) overlaps an approved
        recommended/required chunk is flagged (advisory; never blocks auto-approve).
        Passing supersedes_id (superseding an existing chunk) is a de-escalation path and
        requires the approver key — a plain capture with no supersedes_id is unaffected."""
        if knowledge_type not in KNOWLEDGE_TYPES:
            return {"error": f"invalid_params: knowledge_type must be one of {', '.join(KNOWLEDGE_TYPES)}"}
        if source not in SOURCE_VALUES:
            return {"error": f"invalid_params: source must be one of {', '.join(SOURCE_VALUES)}"}
        if supersedes_id is not None and not require_approver():
            return {
                "error": "not_authorized",
                "hint": "superseding existing knowledge requires the approver key",
            }

        async with get_session_factory()() as session:
            app_repo = AppRepository(session)
            app = await app_repo.get_app(app_slug)
            if not app:
                return {"error": "not_found: app does not exist"}

            content_hash = compute_content_hash(content)
            knowledge_repo = KnowledgeRepository(session)

            existing = await knowledge_repo.find_duplicate(app_slug, knowledge_type, content_hash)
            if existing:
                return {"id": str(existing.id), "duplicate": True, "metadata_summary": existing.metadata_}

            embedding, metadata = await asyncio.gather(
                embed(content),
                extract_metadata(content),
            )

            applicability = {"app_slug": app_slug, "knowledge_type": knowledge_type}
            governance = proposed_defaults(
                proposed_by=proposed_by, applicability=applicability, auto_approve=auto_approve
            )
            flag = await find_conflicts(
                session,
                AppKnowledge,
                candidate_check=None,
                overlap_key_fields=("app_slug", "knowledge_type"),
                candidate=applicability,
            )
            finalize_governance(governance, flag)  # overlap is advisory; never cancels auto-approve

            chunk = await knowledge_repo.create(
                app_id=app.id,
                app_slug=app_slug,
                knowledge_type=knowledge_type,
                content=content,
                content_hash=content_hash,
                embedding=embedding,
                metadata_=metadata,
                source=source,
                supersedes_id=uuid.UUID(supersedes_id) if supersedes_id else None,
                **governance,
            )

            if supersedes_id:
                superseded = await knowledge_repo.get_by_id(uuid.UUID(supersedes_id))
                if not superseded or superseded.app_slug != app_slug:
                    return {"error": "invalid_params: supersedes_id must belong to the same app"}
                await knowledge_repo.supersede(uuid.UUID(supersedes_id), chunk.id)

            await session.commit()

        return {
            "id": str(chunk.id),
            "duplicate": False,
            "metadata_summary": metadata,
            "status": chunk.status,
            "conflict": flag.kind if flag else None,
        }

    @mcp.tool()
    async def delete_knowledge(id: str) -> dict:
        """Soft-delete a knowledge chunk by ID (marks as inactive). APPROVER KEY ONLY."""
        if not require_approver():
            return {"error": "not_authorized", "hint": "delete requires the approver key"}
        async with get_session_factory()() as session:
            repo = KnowledgeRepository(session)
            deactivated = await repo.deactivate(uuid.UUID(id))
            if not deactivated:
                return {"error": "not_found"}
            await session.commit()
        return {"deleted": True, "id": id}

    @mcp.tool()
    async def onboard_app(
        slug: str,
        name: str,
        readme: Optional[str] = None,
        charter: Optional[str] = None,
        architecture_notes: Optional[str] = None,
        deployment_notes: Optional[str] = None,
        replace_existing: bool = False,
        description: Optional[str] = None,
        tech_stack: Optional[dict] = None,
        repo_path: Optional[str] = None,
        deployment_url: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[list[str]] = None,
    ) -> dict:
        """Register an app and seed knowledge from markdown blobs (readme, charter, architecture_notes, deployment_notes). Starts a background job that chunks, classifies, embeds, and stores each piece; returns immediately with onboarding_status='running'. Poll onboard_status(slug) for completion."""
        blobs = {
            "readme": readme,
            "charter": charter,
            "architecture_notes": architecture_notes,
            "deployment_notes": deployment_notes,
        }
        provided_blobs = {k: v for k, v in blobs.items() if v and v.strip()}
        if not provided_blobs:
            return {"error": "invalid_params: at least one onboarding blob is required"}
        if status is not None and status not in APP_STATUSES:
            return {"error": f"invalid_params: status must be one of {', '.join(APP_STATUSES)}"}

        # Blob size limits
        MAX_BLOB_SIZE = 100 * 1024   # 100KB per blob
        MAX_TOTAL_SIZE = 400 * 1024  # 400KB total
        MAX_CHUNKS_PER_ONBOARD = 200

        total_size = sum(len(v.encode()) for v in provided_blobs.values())
        if total_size > MAX_TOTAL_SIZE:
            return {"error": f"invalid_params: total blob size {total_size} exceeds {MAX_TOTAL_SIZE} bytes"}
        for k, v in provided_blobs.items():
            if len(v.encode()) > MAX_BLOB_SIZE:
                return {"error": f"invalid_params: blob '{k}' exceeds {MAX_BLOB_SIZE} bytes"}

        total_pending = sum(len(chunk_text(v)) for v in provided_blobs.values())
        if total_pending > MAX_CHUNKS_PER_ONBOARD:
            return {"error": f"invalid_params: onboarding would produce {total_pending} chunks, max is {MAX_CHUNKS_PER_ONBOARD}"}

        async with get_session_factory()() as session:
            app_repo = AppRepository(session)

            existing = await app_repo.get_app(slug)
            if existing and not replace_existing:
                return {"error": "conflict: app already exists, pass replace_existing=true to update"}

            app_fields: dict = {}
            if description is not None:
                app_fields["description"] = description
            if tech_stack is not None:
                app_fields["tech_stack"] = tech_stack
            if repo_path is not None:
                app_fields["repo_path"] = repo_path
            if deployment_url is not None:
                app_fields["deployment_url"] = deployment_url
            if status is not None:
                app_fields["status"] = status
            if tags is not None:
                app_fields["tags"] = tags

            if existing:
                await app_repo.update_app(slug, name=name, onboarding_status="running", **app_fields)
                app_id = existing.id
            else:
                app = await app_repo.create_app(
                    slug=slug, name=name, onboarding_status="running", **app_fields,
                )
                app_id = app.id

            await session.commit()

        task = asyncio.create_task(
            run_onboarding_job(
                app_id=app_id,
                slug=slug,
                provided_blobs=provided_blobs,
                replace_existing=replace_existing,
            )
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

        logger.info("onboard_scheduled: slug=%s blobs=%s replace=%s", slug, list(provided_blobs.keys()), replace_existing)
        return {
            "app_id": str(app_id),
            "app_slug": slug,
            "onboarding_status": "running",
            "message": "Onboarding started in the background. Poll onboard_status(slug) for completion.",
        }

    @mcp.tool()
    async def onboard_status(slug: str) -> dict:
        """Check onboarding progress for an app. Returns onboarding_status (pending/running/complete/partial/failed), when it last finished, and any error."""
        async with get_session_factory()() as session:
            app = await AppRepository(session).get_app(slug)
            if not app:
                return {"error": "not_found"}
            return {
                "app_slug": slug,
                "onboarding_status": app.onboarding_status,
                "last_onboarded_at": app.last_onboarded_at.isoformat() if app.last_onboarded_at else None,
                "last_onboarding_error": app.last_onboarding_error,
            }
