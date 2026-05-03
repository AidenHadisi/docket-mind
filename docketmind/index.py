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
from llama_index.core.extractors import SummaryExtractor
from llama_index.core.ingestion import IngestionPipeline
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from loguru import logger
from pydantic import BaseModel

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
    """Build the ingestion pipeline with chunking and summary extraction."""
    return IngestionPipeline(
        transformations=[
            SentenceSplitter(
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            ),
            SummaryExtractor(summaries=["self"]),
        ]
    )


# Private singletons: callers go through the module-level functions below
# rather than reaching into the index/pipeline objects directly.
_index: VectorStoreIndex = _build_index()
_pipeline: IngestionPipeline = _build_pipeline()


def _save() -> None:
    """Persist the index to disk."""
    _index.storage_context.persist(persist_dir=str(settings.index_path))


async def upsert_entry(entry: DocketEntry) -> None:
    """Index a docket entry's text into the vector store.

    Runs the entry through the ingestion pipeline (chunking + summary extraction)
    before inserting. Uses entry.id as the document ID for idempotent upserts.
    """
    doc = Document(
        text=f"{entry.title}\n\n{entry.content}",
        doc_id=str(entry.id),
        metadata={
            "case_id": str(entry.case_id),
            "court_listener_id": entry.court_listener_id,
            "date_filed": entry.date_filed.isoformat(),
            "title": entry.title,
            "type": "docket_entry",
        },
    )
    # Delete-then-insert for idempotent upserts: LlamaIndex has no atomic
    # replace, so we purge stale nodes first to avoid duplicates.
    await asyncio.to_thread(_index.delete_ref_doc, str(entry.id), delete_from_docstore=True)
    nodes = await _pipeline.arun(documents=[doc])
    await asyncio.to_thread(_index.insert_nodes, nodes)
    await asyncio.to_thread(_save)


async def delete_case_vectors(case_id: str) -> None:
    """Remove all vector nodes belonging to a case from the index.

    Iterates the docstore and deletes any ref-doc whose metadata
    contains a matching case_id. The full-vector-store JSON dump can
    take seconds, so all blocking work runs in a worker thread to keep
    the event loop responsive (Discord/Slack heartbeats stay alive).
    """

    def _delete() -> bool:
        """Synchronously purge matching ref-docs and persist if anything changed."""
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
        return bool(ref_ids_to_delete)

    await asyncio.to_thread(_delete)


async def upsert_document(
    doc_model: DocketEntryDocument,
    pdf_path: Path,
    date_filed: str = "",
) -> None:
    """Index all pages of a PDF document into the vector store.

    Each page is run through the ingestion pipeline and keyed by
    `<doc_model.id>_page_<n>` for idempotent upserts.

    Requires the llama-index-readers-file package to be installed.
    """
    from llama_index.readers.file import PDFReader  # type: ignore[import-untyped]

    reader = PDFReader()
    pages = await asyncio.to_thread(reader.load_data, file=pdf_path)

    def _purge_stale_pages() -> None:
        """Assign per-page doc IDs and metadata, dropping any prior versions."""
        for i, page in enumerate(pages):
            doc_id = f"{doc_model.id}_page_{i}"
            page.doc_id = doc_id
            page.metadata.update(
                {
                    "docket_entry_id": str(doc_model.docket_entry_id),
                    "pdf_url": doc_model.pdf_url,
                    "date_filed": date_filed,
                    "type": "pdf_document",
                }
            )
            # Purge stale nodes before re-inserting (same pattern as upsert_entry).
            _index.delete_ref_doc(doc_id, delete_from_docstore=True)

    await asyncio.to_thread(_purge_stale_pages)
    nodes = await _pipeline.arun(documents=pages)
    await asyncio.to_thread(_index.insert_nodes, nodes)
    await asyncio.to_thread(_save)


class SourceChunk(BaseModel):
    """A single retrieved chunk used to answer a question."""

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


async def query(question: str, case_id: str | None = None) -> QueryResult:
    """Answer a question using the vector index, optionally scoped to one case.

    Retrieves the most relevant chunks and passes them to the LLM.
    If case_id is provided, only chunks from that case are considered.
    """
    filters = None
    if case_id:
        filters = MetadataFilters(filters=[MetadataFilter(key="case_id", value=case_id)])

    engine = _index.as_query_engine(
        filters=filters,
        similarity_top_k=settings.similarity_top_k,
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
