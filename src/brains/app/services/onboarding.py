import asyncio
import logging
import uuid
from datetime import datetime, timezone

from src.core.db import get_session_factory
from src.brains.app.repositories.apps import AppRepository
from src.brains.app.repositories.knowledge import KnowledgeRepository
from src.brains.app.services.chunker import chunk_text
from src.brains.app.services.classifier import classify_chunk
from src.brains.app.services.hash import compute_content_hash
from src.brains.app.services.openrouter import embed, extract_metadata

logger = logging.getLogger("app_brain.onboarding")

# Default concurrency for background onboarding jobs (matches original app-brain default).
ONBOARD_CONCURRENCY = 6


async def _process_chunk(blob_name: str, chunk_content: str, sem: asyncio.Semaphore) -> dict:
    """Run all the slow LLM I/O for one chunk under a concurrency limit. No DB access."""
    async with sem:
        knowledge_type = await classify_chunk(chunk_content, blob_name)
        embedding, metadata = await asyncio.gather(
            embed(chunk_content),
            extract_metadata(chunk_content),
        )
    return {
        "blob": blob_name,
        "content": chunk_content,
        "content_hash": compute_content_hash(chunk_content),
        "knowledge_type": knowledge_type,
        "embedding": embedding,
        "metadata": metadata,
    }


async def run_onboarding_job(
    app_id: uuid.UUID,
    slug: str,
    provided_blobs: dict[str, str],
    replace_existing: bool,
) -> dict:
    """Background job: chunk -> classify/embed (concurrent) -> store (sequential, two-phase).

    Owns its own DB session. Always lands the app's onboarding_status in a terminal
    state. Returns a summary dict (used by tests; the scheduler ignores it).
    """
    sem = asyncio.Semaphore(ONBOARD_CONCURRENCY)

    total_chunks = 0
    total_skipped = 0
    type_counts: dict[str, int] = {}
    errors: list[dict] = []
    new_chunk_ids: list[uuid.UUID] = []

    logger.info("onboard_start: slug=%s blobs=%s replace=%s", slug, list(provided_blobs.keys()), replace_existing)

    try:
        async with get_session_factory()() as session:
            knowledge_repo = KnowledgeRepository(session)

            # Build the full per-chunk task list across every blob.
            jobs: list[tuple[str, str]] = []
            for blob_name, blob_text in provided_blobs.items():
                for chunk_content in chunk_text(blob_text):
                    jobs.append((blob_name, chunk_content))

            # Phase A.1: all LLM work concurrently (bounded). Exceptions captured per chunk.
            results = await asyncio.gather(
                *[_process_chunk(name, content, sem) for name, content in jobs],
                return_exceptions=True,
            )

            # Phase A.2: write chunks sequentially on the single session.
            for (blob_name, _content), res in zip(jobs, results):
                if isinstance(res, Exception):
                    errors.append({"blob": blob_name, "message": str(res)})
                    continue
                dup = await knowledge_repo.find_duplicate(slug, res["knowledge_type"], res["content_hash"])
                if dup:
                    total_skipped += 1
                    continue
                try:
                    async with session.begin_nested():
                        chunk = await knowledge_repo.create(
                            app_id=app_id,
                            app_slug=slug,
                            knowledge_type=res["knowledge_type"],
                            content=res["content"],
                            content_hash=res["content_hash"],
                            embedding=res["embedding"],
                            metadata_=res["metadata"],
                            source="onboard",
                        )
                        new_chunk_ids.append(chunk.id)
                    total_chunks += 1
                    type_counts[res["knowledge_type"]] = type_counts.get(res["knowledge_type"], 0) + 1
                except Exception as e:
                    errors.append({"blob": res["blob"], "message": str(e)})

            # Phase B: only after new chunks committed do we retire old onboard chunks.
            if replace_existing and total_chunks > 0:
                await knowledge_repo.deactivate_onboard_chunks(slug, exclude_ids=new_chunk_ids)

            await session.commit()

    except Exception as exc:  # noqa: BLE001 — last-resort guard so status never sticks at running
        logger.error("onboard_failed: slug=%s error=%s", slug, exc)
        errors.append({"blob": "_global", "message": str(exc)})

    final_status = (
        "failed" if total_chunks == 0 and errors
        else "partial" if errors
        else "complete"
    )

    # Status update on a fresh session so it succeeds even if the work session is poisoned.
    async with get_session_factory()() as session:
        await AppRepository(session).mark_onboarding_status(
            slug,
            status=final_status,
            error=str(errors) if errors else None,
            onboarded_at=datetime.now(timezone.utc),
        )
        await session.commit()

    logger.info("onboard_complete: slug=%s status=%s chunks=%d errors=%d", slug, final_status, total_chunks, len(errors))
    return {
        "app_id": str(app_id),
        "app_slug": slug,
        "chunks_created": total_chunks,
        "type_counts": type_counts,
        "skipped_duplicates": total_skipped,
        "errors": errors,
    }
