"""Tests for APScheduler per-case job registration."""

from unittest.mock import AsyncMock, patch

import pytest

from docketmind.ingestion.scheduler import _scheduler, add_case, remove_case


@pytest.fixture(autouse=True)
async def fresh_scheduler():
    """Ensure scheduler is stopped and jobs are cleared between tests."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler.remove_all_jobs()
    _scheduler.start()
    yield
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler.remove_all_jobs()


async def test_add_case_registers_interval_job():
    with patch("docketmind.ingestion.scheduler._run_sync", AsyncMock()):
        await add_case("case-001")

    job = _scheduler.get_job("sync_case-001")
    assert job is not None


async def test_add_case_triggers_immediate_sync():
    run_sync = AsyncMock()
    with patch("docketmind.ingestion.scheduler._run_sync", run_sync):
        await add_case("case-001")

    run_sync.assert_awaited_once_with("case-001")


async def test_remove_case_removes_job():
    with patch("docketmind.ingestion.scheduler._run_sync", AsyncMock()):
        await add_case("case-001")

    remove_case("case-001")

    assert _scheduler.get_job("sync_case-001") is None


async def test_remove_case_is_safe_when_job_does_not_exist():
    remove_case("nonexistent-case")  # must not raise
