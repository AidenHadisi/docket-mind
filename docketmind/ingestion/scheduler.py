"""APScheduler configuration for per-case RSS polling jobs."""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger
from sqlalchemy import select

import docketmind.db as db_module
from docketmind.config import settings
from docketmind.ingestion.pipeline import sync_case
from docketmind.models import Case

_scheduler = AsyncIOScheduler()


async def _run_sync(case_id: str) -> None:
    """Run sync_case and log any unhandled errors."""
    try:
        result = await sync_case(case_id)
        if result.errors:
            logger.warning(f"Sync for case {case_id} completed with errors: {result.errors}")
    except Exception as exc:
        logger.error(f"Unhandled error syncing case {case_id}: {exc}")


def _register_job(case_id: str) -> None:
    """Register an interval polling job for a case."""
    _scheduler.add_job(
        _run_sync,
        "interval",
        seconds=settings.poll_interval_seconds,
        args=[case_id],
        id=f"sync_{case_id}",
        replace_existing=True,
    )


async def start() -> None:
    """Start the scheduler and re-register all existing cases from the database."""
    async with db_module.async_session() as session:
        rows = await session.execute(select(Case))
        cases = rows.scalars().all()

    for case in cases:
        _register_job(case.id)

    _scheduler.start()
    logger.info(f"Scheduler started, registered {len(cases)} case(s)")


async def add_case(case_id: str) -> None:
    """Register a polling job for a new case and trigger an immediate backfill."""
    _register_job(case_id)
    await _run_sync(case_id)
    logger.info(f"Added case {case_id} to scheduler and triggered backfill")


def remove_case(case_id: str) -> None:
    """Remove the polling job for a deleted case."""
    job_id = f"sync_{case_id}"
    if _scheduler.get_job(job_id):
        _scheduler.remove_job(job_id)
        logger.info(f"Removed scheduler job for case {case_id}")
