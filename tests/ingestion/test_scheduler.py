"""Tests for APScheduler per-case job registration."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import docketmind.db as db_module
from docketmind.ingestion.scheduler import _scheduler, add_case, remove_case
from docketmind.models import Base, Case


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


@pytest.fixture
async def in_memory_db():
    """Wire up an in-memory SQLite DB for each scheduler test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    original_session = db_module.async_session
    db_module.async_session = async_sessionmaker(engine, expire_on_commit=False)
    yield engine
    db_module.async_session = original_session
    await engine.dispose()


async def test_add_case_registers_interval_job(mocker):
    mocker.patch("docketmind.ingestion.scheduler._run_sync")
    await add_case("case-001")

    assert _scheduler.get_job("sync_case-001") is not None


async def test_add_case_triggers_immediate_sync(mocker):
    run_sync = mocker.patch("docketmind.ingestion.scheduler._run_sync")
    await add_case("case-001")

    run_sync.assert_awaited_once_with("case-001")


async def test_remove_case_removes_job(mocker):
    mocker.patch("docketmind.ingestion.scheduler._run_sync")
    await add_case("case-001")

    remove_case("case-001")

    assert _scheduler.get_job("sync_case-001") is None


async def test_remove_case_is_safe_when_job_does_not_exist():
    remove_case("nonexistent-case")  # must not raise


async def test_start_registers_jobs_for_all_cases(in_memory_db, mocker):
    """start() should register one interval job per case in the DB."""
    from docketmind.ingestion.scheduler import start

    async with db_module.async_session() as session:
        for i in range(2):
            session.add(
                Case(
                    id=f"case-{i:03d}",
                    court_listener_id=f"cl-{i}",
                    name=f"Case {i}",
                    court="D. Mass.",
                )
            )
        await session.commit()

    await start()

    assert _scheduler.get_job("sync_case-000") is not None
    assert _scheduler.get_job("sync_case-001") is not None
