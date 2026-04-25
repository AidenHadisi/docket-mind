"""Root test configuration: shared fixtures and LlamaIndex global init."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import docketmind.index  # noqa: F401 — configures LlamaIndex globals
import docketmind.store as db_module
from docketmind.store import Base


@pytest.fixture
async def in_memory_db():
    """Create an in-memory SQLite database and wire it into the store module.

    Yields the engine for tests that need direct access. Restores the
    original engine and session factory on teardown.
    """
    original_engine = db_module.engine
    original_session = db_module.async_session

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    db_module.engine = engine
    db_module.async_session = async_sessionmaker(engine, expire_on_commit=False)
    yield engine
    db_module.engine = original_engine
    db_module.async_session = original_session
    await engine.dispose()
