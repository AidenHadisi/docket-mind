"""Tests for LlamaIndex vector store indexer."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("llama_index.readers.file", reason="llama-index-readers-file not installed")

from docketmind.ingestion.indexer import get_index, upsert_document, upsert_entry
from docketmind.models import DocketEntry, DocketEntryDocument

_MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
)


@pytest.fixture
def tmp_index_path(tmp_path: Path, monkeypatch):
    """Point settings.index_path to a temp directory by redirecting data_dir."""
    import docketmind.config as cfg

    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    return tmp_path / "index"


@pytest.fixture
def sample_entry() -> DocketEntry:
    return DocketEntry(
        id="entry-001",
        case_id="case-001",
        court_listener_id="cl-001",
        title="Order on Motion to Dismiss",
        content="Court grants defendant's motion to dismiss for lack of jurisdiction.",
        content_hash="abc123",
        date_filed=datetime(2026, 4, 7, tzinfo=UTC),
        embedded=False,
    )


@pytest.fixture
def sample_document() -> DocketEntryDocument:
    return DocketEntryDocument(
        id="doc-001",
        docket_entry_id="entry-001",
        pdf_url="https://storage.courtlistener.com/recap/doc.pdf",
        downloaded=True,
    )


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    """Write a minimal valid PDF and return its path."""
    path = tmp_path / "test.pdf"
    path.write_bytes(_MINIMAL_PDF)
    return path


def test_get_index_creates_index_directory(tmp_index_path: Path):
    get_index()
    assert tmp_index_path.exists()


def test_upsert_entry_indexes_without_error(tmp_index_path: Path, sample_entry: DocketEntry):
    index = get_index()
    upsert_entry(index, sample_entry)  # should not raise


def test_upsert_entry_is_idempotent(tmp_index_path: Path, sample_entry: DocketEntry):
    index = get_index()
    upsert_entry(index, sample_entry)
    upsert_entry(index, sample_entry)

    fresh_index = get_index()
    doc_count = len(fresh_index.docstore.docs)
    assert doc_count == 1, f"Expected 1 doc after idempotent upsert, got {doc_count}"


def test_upsert_document_indexes_without_error(
    tmp_index_path: Path, sample_document: DocketEntryDocument, pdf_path: Path
):
    """upsert_document should not raise when given a valid PDF path."""
    index = get_index()
    upsert_document(index, sample_document, pdf_path)  # should not raise


def test_upsert_document_is_idempotent(
    tmp_index_path: Path, sample_document: DocketEntryDocument, pdf_path: Path
):
    """Calling upsert_document twice with the same doc must not duplicate pages."""
    index = get_index()
    upsert_document(index, sample_document, pdf_path)
    upsert_document(index, sample_document, pdf_path)

    fresh_index = get_index()
    doc_count = len(fresh_index.docstore.docs)
    assert doc_count == 1, f"Expected 1 doc after idempotent upsert, got {doc_count}"
