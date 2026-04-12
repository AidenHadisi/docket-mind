"""Tests for SQLAlchemy ORM models."""

import re
from datetime import UTC, datetime

from docketmind.models import Case, DocketEntry, DocketEntryDocument

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def test_case_rss_url_derived_from_court_listener_id():
    case = Case(
        court_listener_id="12345678",
        name="United States v. Doe",
        court="D. Mass.",
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
    case = Case(court_listener_id="abc", name="Test", court="D. Mass.")
    assert UUID_RE.match(case.id), f"Expected UUID, got: {case.id}"


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
    assert UUID_RE.match(entry.id), f"Expected UUID, got: {entry.id}"
