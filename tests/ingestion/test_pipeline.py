"""Tests for the case sync pipeline."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import docketmind.db as db_module
from docketmind.ingestion.pipeline import SyncResult, sync_case
from docketmind.ingestion.rss import RawEntry
from docketmind.models import Base, Case


@pytest.fixture(autouse=True)
async def in_memory_db():
    """Wire up an in-memory SQLite DB for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db_module.engine = engine
    db_module.async_session = async_sessionmaker(engine, expire_on_commit=False)
    yield
    await engine.dispose()


@pytest.fixture
async def saved_case() -> Case:
    """Insert a Case into the in-memory DB and return it."""
    async with db_module.async_session() as session:
        case = Case(
            id="case-001",
            court_listener_id="12345",
            name="United States v. Doe",
            court="D. Mass.",
        )
        session.add(case)
        await session.commit()
    return case


@pytest.fixture
def raw_entry_no_pdf() -> RawEntry:
    return RawEntry(
        court_listener_id="cl-001",
        title="Order on Motion",
        content="Court rules on motion.",
        content_hash="hash-001",
        date_filed=datetime(2026, 4, 7, tzinfo=UTC),
        pdf_urls=[],
    )


@pytest.fixture
def raw_entry_with_pdf() -> RawEntry:
    return RawEntry(
        court_listener_id="cl-002",
        title="Filed Motion",
        content="Defendant files motion.",
        content_hash="hash-002",
        date_filed=datetime(2026, 4, 8, tzinfo=UTC),
        pdf_urls=["https://storage.courtlistener.com/recap/doc.pdf"],
    )


@pytest.fixture
def pipeline_mocks(mocker):
    """Patch all external pipeline dependencies and return the mocks."""
    return {
        "fetch_feed": mocker.patch("docketmind.ingestion.pipeline.fetch_feed"),
        "get_index": mocker.patch(
            "docketmind.ingestion.pipeline.get_index", return_value=MagicMock()
        ),
        "upsert_entry": mocker.patch("docketmind.ingestion.pipeline.upsert_entry"),
        "update_case_memory": mocker.patch(
            "docketmind.ingestion.pipeline.update_case_memory", return_value="summary"
        ),
    }


async def test_sync_case_returns_sync_result_for_unknown_case():
    result = await sync_case("nonexistent-id")
    assert isinstance(result, SyncResult)
    assert result.errors


async def test_sync_case_inserts_new_entries(saved_case, raw_entry_no_pdf, pipeline_mocks):
    pipeline_mocks["fetch_feed"].return_value = [raw_entry_no_pdf]

    result = await sync_case("case-001")

    assert result.new_entries == 1
    assert result.updated_entries == 0


async def test_sync_case_detects_changed_entry(saved_case, raw_entry_no_pdf, pipeline_mocks):
    # First sync: insert the entry
    pipeline_mocks["fetch_feed"].return_value = [raw_entry_no_pdf]
    await sync_case("case-001")

    # Second sync: same entry but different hash
    changed_entry = raw_entry_no_pdf.model_copy(update={"content_hash": "new-hash"})
    pipeline_mocks["fetch_feed"].return_value = [changed_entry]
    result = await sync_case("case-001")

    assert result.updated_entries == 1
    assert result.new_entries == 0


async def test_sync_case_is_idempotent(saved_case, raw_entry_no_pdf, pipeline_mocks):
    """Running sync twice with unchanged entries produces zero new/updated on second run."""
    pipeline_mocks["fetch_feed"].return_value = [raw_entry_no_pdf]

    await sync_case("case-001")
    result = await sync_case("case-001")

    assert result.new_entries == 0
    assert result.updated_entries == 0
