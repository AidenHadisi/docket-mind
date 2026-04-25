"""Tests for SQLAlchemy ORM models."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

import docketmind.store as db_module
from docketmind.store import Case, DocketEntry, DocketEntryDocument


def test_case_rss_url_derived_from_court_listener_id():
    case = Case(
        court_listener_id="12345678",
        name="United States v. Doe",
    )
    assert case.rss_url == "https://www.courtlistener.com/docket/12345678/feed/"


def test_docket_entry_defaults_embedded_false():
    entry = DocketEntry(
        case_id="some-uuid",
        court_listener_id="entry-001",
        title="Order on Motion",
        content="Court grants motion to dismiss.",
        content_hash="abc123",
        date_filed=datetime(2026, 1, 15, tzinfo=UTC),
    )
    assert entry.embedded is False


def test_docket_entry_document_defaults():
    doc = DocketEntryDocument(
        docket_entry_id="entry-uuid",
        pdf_url="https://storage.courtlistener.com/recap/doc.pdf",
    )
    assert doc.downloaded is False
    assert doc.embedded is False
    assert doc.pdf_path is None


def test_case_id_is_uuid_at_construction():
    """Case.id must be a valid UUID string immediately after __init__, before any flush."""
    case = Case(court_listener_id="abc", name="Test")
    UUID(case.id)


def test_docket_entry_id_is_uuid_at_construction():
    """DocketEntry.id must be a valid UUID string immediately after __init__, before any flush."""
    entry = DocketEntry(
        case_id="some-uuid",
        court_listener_id="cl-001",
        title="Order",
        content="Text.",
        content_hash="hash",
        date_filed=datetime(2026, 1, 1, tzinfo=UTC),
    )
    UUID(entry.id)


async def test_case_can_be_saved_and_retrieved(in_memory_db):
    """A Case instance can be persisted and read back from the DB."""
    async with db_module.async_session() as session:
        case = Case(
            court_listener_id="99999",
            name="Test v. Case",
        )
        session.add(case)
        await session.commit()

    async with db_module.async_session() as session:
        result = await session.execute(select(Case).where(Case.court_listener_id == "99999"))
        saved = result.scalar_one()
        assert saved.name == "Test v. Case"
        assert saved.rss_url == "https://www.courtlistener.com/docket/99999/feed/"
