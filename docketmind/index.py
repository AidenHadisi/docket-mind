"""Vector store: ingest upserts and RAG query, sharing a single private index."""

import asyncio
import shutil
from pathlib import Path
from typing import cast

from llama_index.core import (
    Document,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.postprocessor import FixedRecencyPostprocessor
from llama_index.core.query_engine import RetrieverQueryEngine
from llama_index.core.retrievers import QueryFusionRetriever
from llama_index.core.retrievers.fusion_retriever import FUSION_MODES
from llama_index.core.schema import BaseNode
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from llama_index.retrievers.bm25 import BM25Retriever
from loguru import logger
from pydantic import BaseModel, ConfigDict

from docketmind.configure import settings
from docketmind.prompts import DOCKET_QA_TEMPLATE, DOCKET_REFINE_TEMPLATE
from docketmind.store import DocketEntry, DocketEntryDocument


def _create_empty_index(index_path: Path) -> VectorStoreIndex:
    """Initialise a fresh empty vector index at index_path."""
    index_path.mkdir(parents=True, exist_ok=True)
    store = VectorStoreIndex([], storage_context=StorageContext.from_defaults())
    store.storage_context.persist(persist_dir=str(index_path))
    return store


def _build_index() -> VectorStoreIndex:
    """Load the persisted vector index from disk, or create a new empty one.

    If the on-disk index is corrupt (e.g. a process crashed mid-persist and
    left a zero-byte JSON file), wipe the broken state and start fresh.
    The SQLite DB is the source of truth — the scheduler will re-index
    every tracked case on startup.
    """
    index_path = settings.index_path

    if index_path.is_dir() and any(index_path.iterdir()):
        try:
            storage_context = StorageContext.from_defaults(persist_dir=str(index_path))
            return cast(VectorStoreIndex, load_index_from_storage(storage_context))
        except Exception as exc:
            logger.warning(
                "Vector index at {} is corrupt ({}). Wiping and starting fresh; "
                "the scheduler will re-index tracked cases on startup.",
                index_path,
                exc,
            )
            shutil.rmtree(index_path)

    return _create_empty_index(index_path)


def _build_pipeline() -> IngestionPipeline:
    """Build the chunking-only ingestion pipeline.

    SummaryExtractor was tried but added an LLM call per chunk for no
    measurable retrieval benefit alongside BM25 + recency reranking.
    """
    return IngestionPipeline(
        transformations=[
            SentenceSplitter(
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            ),
        ]
    )


_index: VectorStoreIndex = _build_index()
_pipeline: IngestionPipeline = _build_pipeline()

# VectorStoreIndex is not thread-safe and is touched by many independent async
# tasks (scheduler writers, query readers, command-driven deletes); without
# this lock concurrent access on docstore.docs raises "dictionary changed size
# during iteration". Every public function below that touches _index holds it.
sync_lock = asyncio.Lock()


def _save() -> None:
    """Persist the index to disk."""
    _index.storage_context.persist(persist_dir=str(settings.index_path))


async def upsert_entry(entry: DocketEntry) -> None:
    """Index a docket entry into the vector store.

    The text is prefixed with a `[Filed YYYY-MM-DD - title]` header so prompts
    can apply the prefer-most-recent rule from in-band evidence. Uses
    entry.id as the document ID for idempotent upserts.
    """
    date_filed_str = entry.date_filed.strftime("%Y-%m-%d")
    header = f"[Filed {date_filed_str} - {entry.title}]"
    doc = Document(
        text=f"{header}\n\n{entry.title}\n\n{entry.content}",
        doc_id=str(entry.id),
        metadata={
            "case_id": str(entry.case_id),
            "court_listener_id": entry.court_listener_id,
            "date_filed": date_filed_str,
            "title": entry.title,
            "type": "docket_entry",
        },
    )
    nodes = await _pipeline.arun(documents=[doc])

    def _apply() -> None:
        """Purge any prior version of the entry, then insert and persist."""
        _index.delete_ref_doc(str(entry.id), delete_from_docstore=True)
        _index.insert_nodes(nodes)
        _save()

    async with sync_lock:
        await asyncio.to_thread(_apply)


async def delete_case_vectors(case_id: str) -> None:
    """Remove all vector nodes belonging to a case from the index."""

    def _delete() -> None:
        """Find ref-docs tagged with case_id, delete them, and persist if any matched."""
        docstore = _index.storage_context.docstore
        all_ref_docs = docstore.get_all_ref_doc_info()
        ref_ids_to_delete = [
            ref_id
            for ref_id, doc_info in (all_ref_docs or {}).items()
            if doc_info.metadata.get("case_id") == case_id
        ]
        for ref_id in ref_ids_to_delete:
            _index.delete_ref_doc(ref_id, delete_from_docstore=True)
        if ref_ids_to_delete:
            _save()

    async with sync_lock:
        await asyncio.to_thread(_delete)


async def upsert_document(
    doc_model: DocketEntryDocument,
    pdf_path: Path,
    date_filed: str = "",
    title: str = "",
) -> None:
    """Index all pages of a PDF document into the vector store.

    Each page is keyed by `<doc_model.id>_page_<n>` for idempotent upserts and
    prefixed with the same `[Filed YYYY-MM-DD - title]` header as upsert_entry.
    Requires the llama-index-readers-file package.
    """
    from llama_index.readers.file import PDFReader  # type: ignore[import-untyped]

    reader = PDFReader()
    pages = await asyncio.to_thread(reader.load_data, file=pdf_path)

    # Slice handles both full ISO ("2026-04-07T07:00:00+00:00") and bare date.
    date_filed_str = date_filed[:10]
    header = f"[Filed {date_filed_str} - {title}]" if (date_filed_str or title) else ""

    page_doc_ids: list[str] = []
    for i, page in enumerate(pages):
        doc_id = f"{doc_model.id}_page_{i}"
        page.doc_id = doc_id
        page_doc_ids.append(doc_id)
        page.metadata.update(
            {
                "docket_entry_id": str(doc_model.docket_entry_id),
                "pdf_url": doc_model.pdf_url,
                "date_filed": date_filed_str,
                "title": title,
                "type": "pdf_document",
            }
        )
        if header:
            page.set_content(f"{header}\n\n{page.get_content()}")

    nodes = await _pipeline.arun(documents=pages)

    def _apply() -> None:
        """Purge prior page versions, then insert and persist."""
        for doc_id in page_doc_ids:
            _index.delete_ref_doc(doc_id, delete_from_docstore=True)
        _index.insert_nodes(nodes)
        _save()

    async with sync_lock:
        await asyncio.to_thread(_apply)


class SourceChunk(BaseModel):
    """A single retrieved chunk used to answer a question."""

    # Tolerate unknown metadata keys so adding one in upsert_* doesn't break retrieval.
    model_config = ConfigDict(extra="ignore")

    text: str
    score: float
    type: str
    case_id: str | None = None
    court_listener_id: str | None = None
    date_filed: str | None = None
    title: str | None = None
    docket_entry_id: str | None = None
    pdf_url: str | None = None


class QueryResult(BaseModel):
    """The result of a RAG query, including the answer and its source chunks."""

    answer: str
    sources: list[SourceChunk]


async def _build_retriever(case_id: str | None) -> BaseRetriever:
    """Build a hybrid (vector + BM25) retriever, optionally scoped to one case.

    Vector retrieval handles semantic matches; BM25 catches exact-token matches
    embeddings miss (party names, judges, docket numbers). Results are fused
    with reciprocal rank fusion. Falls back to vector-only when the docstore
    is empty or the case_id filter eliminates every node, since
    `BM25Retriever.from_defaults` raises on an empty node list.
    """
    filters = None
    if case_id:
        filters = MetadataFilters(filters=[MetadataFilter(key="case_id", value=case_id)])

    vector_retriever = _index.as_retriever(
        filters=filters,
        similarity_top_k=settings.similarity_top_k,
    )

    # Snapshot under the lock to avoid racing a concurrent upsert.
    async with sync_lock:
        nodes: list[BaseNode] = list(_index.docstore.docs.values())
    if case_id:
        nodes = [n for n in nodes if n.metadata.get("case_id") == case_id]
    if not nodes:
        return vector_retriever

    # Clamp top_k to silence BM25's "overriding similarity_top_k" warning.
    bm25_retriever = BM25Retriever.from_defaults(
        nodes=nodes,
        similarity_top_k=min(settings.similarity_top_k, len(nodes)),
    )

    # num_queries=1 disables LLM-based query rewriting; we only want hybrid fusion.
    return QueryFusionRetriever(
        [vector_retriever, bm25_retriever],
        similarity_top_k=settings.similarity_top_k,
        num_queries=1,
        mode=FUSION_MODES.RECIPROCAL_RANK,
        use_async=True,
    )


async def query(question: str, case_id: str | None = None) -> QueryResult:
    """Answer a question using hybrid retrieval, optionally scoped to one case.

    Retrieves candidate chunks from both vector search and BM25, fuses them
    with reciprocal rank fusion, reranks by recency, and passes the survivors
    to the LLM. If case_id is provided, only chunks from that case are
    considered.
    """
    retriever = await _build_retriever(case_id)

    engine = RetrieverQueryEngine.from_args(
        retriever,
        node_postprocessors=[
            FixedRecencyPostprocessor(
                date_key="date_filed",
                top_k=settings.synthesis_top_k,
            ),
        ],
        text_qa_template=DOCKET_QA_TEMPLATE,
        refine_template=DOCKET_REFINE_TEMPLATE,
    )
    response = await engine.aquery(question)

    sources = [
        SourceChunk(
            text=node.text,
            score=node.score or 0.0,
            **node.metadata,
        )
        for node in response.source_nodes
    ]

    return QueryResult(answer=str(response), sources=sources)
