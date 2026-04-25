"""Tests for the case sync pipeline."""

from datetime import UTC, datetime

import pytest

import docketmind.store as db_module
from docketmind.ingest import RawEntry, SyncResult, sync_case
from docketmind.store import Case


@pytest.fixture(autouse=True)
async def _db(in_memory_db):
    """Auto-use the shared in_memory_db fixture for every test in this module."""


@pytest.fixture
async def saved_case() -> Case:
    """Insert a Case into the in-memory DB and return it."""
    async with db_module.async_session() as session:
        case = Case(
            court_listener_id="12345",
            name="United States v. Doe",
        )
        session.add(case)
        await session.commit()
    return case


@pytest.fixture
def raw_entry_no_pdf() -> RawEntry:
    """A RawEntry with no PDF attachments."""
    return RawEntry(
        court_listener_id="cl-001",
        title="Order on Motion",
        content="Court rules on motion.",
        date_filed=datetime(2026, 4, 7, tzinfo=UTC),
        pdf_urls=[],
    )


@pytest.fixture
def raw_entry_with_pdf() -> RawEntry:
    """A RawEntry with one PDF attachment."""
    return RawEntry(
        court_listener_id="cl-002",
        title="Filed Motion",
        content="Defendant files motion.",
        date_filed=datetime(2026, 4, 8, tzinfo=UTC),
        pdf_urls=["https://storage.courtlistener.com/recap/doc.pdf"],
    )


@pytest.fixture
def pipeline_mocks(mocker):
    """Patch all external pipeline dependencies and return the mocks."""
    return {
        "fetch_feed": mocker.patch("docketmind.ingest.fetch_feed"),
        "upsert_entry": mocker.patch("docketmind.ingest.upsert_entry"),
    }


async def test_sync_case_returns_sync_result_for_unknown_case():
    result = await sync_case("nonexistent-id")
    assert isinstance(result, SyncResult)
    assert result.errors


async def test_sync_case_inserts_new_entries(saved_case, raw_entry_no_pdf, pipeline_mocks):
    pipeline_mocks["fetch_feed"].return_value = [raw_entry_no_pdf]

    result = await sync_case(saved_case.id)

    assert result.new_entries == 1
    assert result.updated_entries == 0


async def test_sync_case_detects_changed_entry(saved_case, raw_entry_no_pdf, pipeline_mocks):
    # First sync: insert the entry
    pipeline_mocks["fetch_feed"].return_value = [raw_entry_no_pdf]
    await sync_case(saved_case.id)

    # Second sync: same entry but updated content (produces a different hash)
    changed_entry = raw_entry_no_pdf.model_copy(update={"content": "Updated court ruling."})
    pipeline_mocks["fetch_feed"].return_value = [changed_entry]
    result = await sync_case(saved_case.id)

    assert result.updated_entries == 1
    assert result.new_entries == 0


async def test_sync_case_is_idempotent(saved_case, raw_entry_no_pdf, pipeline_mocks):
    """Running sync twice with unchanged entries produces zero new/updated on second run."""
    pipeline_mocks["fetch_feed"].return_value = [raw_entry_no_pdf]

    await sync_case(saved_case.id)
    result = await sync_case(saved_case.id)

    assert result.new_entries == 0
    assert result.updated_entries == 0
