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
    run_sync = mocker.patch("docketmind.schedule._run_sync")
    await add_case("case-001")

    run_sync.assert_awaited_once_with("case-001")


async def test_remove_case_removes_job(mocker):
    mocker.patch("docketmind.schedule._run_sync")
    await add_case("case-001")

    remove_case("case-001")

    assert _scheduler.get_job("sync_case-001") is None


async def test_remove_case_is_safe_when_job_does_not_exist():
    remove_case("nonexistent-case")  # must not raise


async def test_start_registers_jobs_for_all_cases(in_memory_db, mocker):
    """start() should register one interval job per case in the DB."""
    from docketmind.schedule import start

    cases = [
        Case(court_listener_id=f"cl-{i}", name=f"Case {i}", court="D. Mass.") for i in range(2)
    ]
    async with db_module.async_session() as session:
        session.add_all(cases)
        await session.commit()

    await start()

    for case in cases:
        assert _scheduler.get_job(f"sync_{case.id}") is not None
