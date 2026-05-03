"""Tests for APScheduler per-case job registration."""

import pytest

import docketmind.store as db_module
from docketmind.schedule import _scheduler, add_case, remove_case
from docketmind.store import Case


@pytest.fixture(autouse=True)
async def fresh_scheduler():
    """Ensure scheduler jobs are cleared between tests."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler.remove_all_jobs()
    yield
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler.remove_all_jobs()


async def test_add_case_registers_interval_job(mocker):
    mocker.patch("docketmind.schedule._run_sync")
    await add_case("case-001")

    assert _scheduler.get_job("sync_case-001") is not None


async def test_add_case_triggers_immediate_sync(mocker):
    """Backfill is fired as a background task; yield control so it runs."""
    import asyncio

    run_sync = mocker.patch("docketmind.schedule._run_sync")
    await add_case("case-001")
    await asyncio.sleep(0)

    run_sync.assert_awaited_once_with("case-001")


async def test_remove_case_removes_job(mocker):
    mocker.patch("docketmind.schedule._run_sync")
    await add_case("case-001")

    remove_case("case-001")

    assert _scheduler.get_job("sync_case-001") is None


async def test_remove_case_is_safe_when_job_does_not_exist():
    remove_case("nonexistent-case")  # must not raise


async def test_run_sync_serializes_via_index_lock(mocker):
    """Two concurrent _run_sync calls must not overlap inside sync_case.

    The fix for the docstore race depends on `index.sync_lock` serialising
    every writer; if anyone removes the `async with` in `_run_sync` this
    test will see overlap and fail.
    """
    import asyncio

    from docketmind.schedule import _run_sync

    overlapping = 0
    max_overlapping = 0

    async def slow_sync(case_id: str):
        nonlocal overlapping, max_overlapping
        overlapping += 1
        max_overlapping = max(max_overlapping, overlapping)
        await asyncio.sleep(0.05)
        overlapping -= 1
        return mocker.MagicMock(errors=[])

    mocker.patch("docketmind.schedule.sync_case", side_effect=slow_sync)

    await asyncio.gather(_run_sync("a"), _run_sync("b"), _run_sync("c"))

    assert max_overlapping == 1, (
        f"expected serialised sync_case execution, observed {max_overlapping} concurrent"
    )


async def test_start_registers_jobs_for_all_cases(in_memory_db, mocker):
    """start() should register one interval job per case in the DB."""
    from docketmind.schedule import start

    cases = [Case(court_listener_id=f"cl-{i}", name=f"Case {i}") for i in range(2)]
    async with db_module.async_session() as session:
        session.add_all(cases)
        await session.commit()

    await start()

    for case in cases:
        assert _scheduler.get_job(f"sync_{case.id}") is not None
