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
    monkeypatch.setattr(ll, "_index", fresh)
    # Replace the production pipeline (which has SummaryExtractor) with a
    # lightweight one so tests don't make LLM calls.
    monkeypatch.setattr(
        ll,
        "_pipeline",
        IngestionPipeline(transformations=[SentenceSplitter()]),
    )
    return fresh


@pytest.fixture
def sample_entry() -> DocketEntry:
    """A minimal DocketEntry for vector-store indexing tests."""
    entry = DocketEntry(
        case_id="case-001",
        court_listener_id="cl-001",
        title="Order on Motion to Dismiss",
        content="Court grants defendant's motion to dismiss for lack of jurisdiction.",
        content_hash="abc123",
        date_filed=datetime(2026, 4, 7, tzinfo=UTC),
        embedded=False,
    )
    object.__setattr__(entry, "id", "entry-001")
    return entry


@pytest.fixture
def sample_document(sample_entry: DocketEntry) -> DocketEntryDocument:
    """A minimal DocketEntryDocument linked to sample_entry."""
    doc = DocketEntryDocument(
        docket_entry_id=sample_entry.id,
        pdf_url="https://storage.courtlistener.com/recap/doc.pdf",
        downloaded=True,
    )
    object.__setattr__(doc, "id", "doc-001")
    return doc


@pytest.fixture
def pdf_path(tmp_path: Path) -> Path:
    """Write a minimal valid PDF and return its path."""
    path = tmp_path / "test.pdf"
    path.write_bytes(_MINIMAL_PDF)
    return path


def test_index_directory_is_created(tmp_index, tmp_path: Path):
    assert (tmp_path / "index").exists()


async def test_upsert_entry_indexes_without_error(tmp_index, sample_entry: DocketEntry):
    await upsert_entry(sample_entry)


async def test_upsert_entry_is_idempotent(tmp_index, sample_entry: DocketEntry):
    await upsert_entry(sample_entry)
    await upsert_entry(sample_entry)

    doc_count = len(tmp_index.docstore.docs)
    assert doc_count == 1, f"Expected 1 doc after idempotent upsert, got {doc_count}"


async def test_upsert_document_indexes_without_error(
    tmp_index, sample_document: DocketEntryDocument, pdf_path: Path
):
    await upsert_document(sample_document, pdf_path)


async def test_upsert_document_is_idempotent(
    tmp_index, sample_document: DocketEntryDocument, pdf_path: Path
):
    await upsert_document(sample_document, pdf_path)
    await upsert_document(sample_document, pdf_path)

    doc_count = len(tmp_index.docstore.docs)
    assert doc_count == 1, f"Expected 1 doc after idempotent upsert, got {doc_count}"


async def test_upsert_entry_includes_filed_header(tmp_index, sample_entry: DocketEntry):
    """The synthetic [Filed YYYY-MM-DD - title] header is embedded in node text."""
    await upsert_entry(sample_entry)

    nodes = list(tmp_index.docstore.docs.values())
    assert nodes, "expected at least one node after upsert"
    expected_header = "[Filed 2026-04-07 - Order on Motion to Dismiss]"
    assert any(node.text.startswith(expected_header) for node in nodes), (
        f"no node started with {expected_header!r}; got: {[n.text[:80] for n in nodes]}"
    )


async def test_build_retriever_falls_back_to_vector_when_empty(tmp_index):
    """With no nodes indexed, hybrid retrieval can't build BM25; fall back."""
    from llama_index.core.retrievers import QueryFusionRetriever, VectorIndexRetriever

    from docketmind.index import _build_retriever

    retriever = await _build_retriever(case_id=None)
    assert isinstance(retriever, VectorIndexRetriever)
    assert not isinstance(retriever, QueryFusionRetriever)


async def test_build_retriever_uses_hybrid_when_nodes_exist(tmp_index, sample_entry: DocketEntry):
    """With nodes in the docstore, _build_retriever returns a fusion retriever."""
    from llama_index.core.retrievers import QueryFusionRetriever

    from docketmind.index import _build_retriever, upsert_entry

    await upsert_entry(sample_entry)

    retriever = await _build_retriever(case_id=None)
    assert isinstance(retriever, QueryFusionRetriever)


async def test_query_reranks_to_most_recent(tmp_index, monkeypatch):
    """FixedRecencyPostprocessor surfaces the most recent entry first."""
    from llama_index.core import Settings as LlamaConfig
    from llama_index.core.llms.mock import MockLLM

    from docketmind.index import query

    monkeypatch.setattr(LlamaConfig, "llm", MockLLM())

    older = DocketEntry(
        case_id="case-001",
        court_listener_id="cl-old",
        title="Order Setting Initial Conference",
        content="Court schedules an initial case management conference.",
        content_hash="hash-old",
        date_filed=datetime(2026, 1, 1, tzinfo=UTC),
        embedded=False,
    )
    object.__setattr__(older, "id", "entry-old")
    newer = DocketEntry(
        case_id="case-001",
        court_listener_id="cl-new",
        title="Order on Motion to Dismiss",
        content="Court grants defendant's motion to dismiss for lack of jurisdiction.",
        content_hash="hash-new",
        date_filed=datetime(2026, 4, 7, tzinfo=UTC),
        embedded=False,
    )
    object.__setattr__(newer, "id", "entry-new")

    await upsert_entry(older)
    await upsert_entry(newer)

    result = await query("what is the latest", case_id="case-001")

    assert result.sources, "expected at least one source chunk"
    top = result.sources[0]
    assert top.date_filed is not None and top.date_filed.startswith("2026-04-07"), (
        f"expected top source to be the 2026-04-07 entry, got {top.date_filed!r}"
    )
