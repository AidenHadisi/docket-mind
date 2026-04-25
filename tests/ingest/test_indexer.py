"""Tests for LlamaIndex vector store indexer."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("llama_index.readers.file", reason="llama-index-readers-file not installed")

from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter

from docketmind.index import upsert_document, upsert_entry
from docketmind.store import DocketEntry, DocketEntryDocument

_MINIMAL_PDF = (
    b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
)


@pytest.fixture
def tmp_index(tmp_path: Path, monkeypatch):
    """Build a fresh index and a no-LLM pipeline in a temp directory."""
    import docketmind.configure as cfg
    import docketmind.index as ll
    from docketmind.index import _build_index

    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    fresh = _build_index()
    monkeypatch.setattr(ll, "index", fresh)
    # Replace the production pipeline (which has SummaryExtractor) with a
    # lightweight one so tests don't make LLM calls.
    monkeypatch.setattr(
        ll,
        "pipeline",
        IngestionPipeline(transformations=[SentenceSplitter()]),
    )
    return fresh


@pytest.fixture
def sample_entry() -> DocketEntry:
    """A minimal DocketEntry for vector-store indexing tests."""
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
    """A minimal DocketEntryDocument linked to sample_entry."""
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


def test_index_directory_is_created(_tmp_index, tmp_path: Path):
    assert (tmp_path / "index").exists()


async def test_upsert_entry_indexes_without_error(_tmp_index, sample_entry: DocketEntry):
    await upsert_entry(sample_entry)


async def test_upsert_entry_is_idempotent(tmp_index, sample_entry: DocketEntry):
    await upsert_entry(sample_entry)
    await upsert_entry(sample_entry)

    doc_count = len(tmp_index.docstore.docs)
    assert doc_count == 1, f"Expected 1 doc after idempotent upsert, got {doc_count}"


async def test_upsert_document_indexes_without_error(
    _tmp_index, sample_document: DocketEntryDocument, pdf_path: Path
):
    await upsert_document(sample_document, pdf_path)


async def test_upsert_document_is_idempotent(
    tmp_index, sample_document: DocketEntryDocument, pdf_path: Path
):
    await upsert_document(sample_document, pdf_path)
    await upsert_document(sample_document, pdf_path)

    doc_count = len(tmp_index.docstore.docs)
    assert doc_count == 1, f"Expected 1 doc after idempotent upsert, got {doc_count}"
