"""APScheduler configuration for per-case RSS polling jobs."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

import docketmind.store as store
from docketmind.configure import settings
from docketmind.ingest import sync_case
from docketmind.store import list_cases

_scheduler = AsyncIOScheduler()


async def _run_sync(case_id: str) -> None:
    """Run sync_case and log any unhandled errors.

    No lock here: `max_instances=1` prevents same-case overlap, and
    cross-case writes serialise inside the `index.*` operations themselves.
    """
    try:
        result = await sync_case(case_id)
        if result.errors:
            logger.warning("Sync for case {} completed with errors: {}", case_id, result.errors)
    except Exception as exc:
        logger.error("Unhandled error syncing case {}: {}", case_id, exc)


def _register_job(case_id: str) -> None:
    """Register an interval polling job for a case."""
    _scheduler.add_job(
        _run_sync,
        "interval",
        seconds=settings.poll_interval_seconds,
        args=[case_id],
        id=f"sync_{case_id}",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


async def start() -> None:
    """Start the scheduler and re-register all existing cases from the database."""
    async with store.async_session() as session:
        cases = await list_cases(session)

    for case in cases:
        _register_job(case.id)

    _scheduler.start()
    logger.info("Scheduler started, registered {} case(s)", len(cases))


async def add_case(case_id: str) -> None:
    """Register a polling job for a new case and trigger an immediate backfill.

    The backfill runs as a background task so the caller (e.g. a slash
    command handler) can return a response immediately.
    """
    import asyncio

    _register_job(case_id)
    asyncio.create_task(_run_sync(case_id), name=f"backfill-{case_id}")
    logger.info("Added case {} to scheduler and triggered backfill", case_id)


def remove_case(case_id: str) -> None:
    """Remove the polling job for a deleted case."""
    job_id = f"sync_{case_id}"
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
        logger.info("Removed scheduler job for case {}", case_id)
