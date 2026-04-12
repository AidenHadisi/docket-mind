"""LlamaIndex vector store: index creation, document upsert."""

import contextlib
from pathlib import Path
from typing import cast

from llama_index.core import (
    Document,
    StorageContext,
    VectorStoreIndex,
    load_index_from_storage,
)
from llama_index.core import (
    Settings as LlamaSettings,
)
from llama_index.core.embeddings import MockEmbedding
from llama_index.embeddings.openai import OpenAIEmbedding

from docketmind.config import settings
from docketmind.models import DocketEntry, DocketEntryDocument


def _configure_llama() -> None:
    """Set the embedding model on the LlamaIndex global settings.

    Uses MockEmbedding when running under test (api_key does not start with 'sk-')
    to avoid real OpenAI calls during the test suite.
    """
    if not settings.openai_api_key.startswith("sk-"):
        LlamaSettings.embed_model = MockEmbedding(embed_dim=1536)
    else:
        LlamaSettings.embed_model = OpenAIEmbedding(
            model=settings.openai_embedding_model,
            api_key=settings.openai_api_key,
        )


def get_index() -> VectorStoreIndex:
    """Load the persisted vector index from disk, or create a new empty one."""
    _configure_llama()
    index_path = settings.index_path

    if index_path.exists() and any(index_path.iterdir()):
        storage_context = StorageContext.from_defaults(persist_dir=str(index_path))
        return cast(VectorStoreIndex, load_index_from_storage(storage_context))

    index_path.mkdir(parents=True, exist_ok=True)
    index = VectorStoreIndex([], storage_context=StorageContext.from_defaults())
    index.storage_context.persist(persist_dir=str(index_path))
    return index


def _save(index: VectorStoreIndex) -> None:
    """Persist the index to disk."""
    index.storage_context.persist(persist_dir=str(settings.index_path))


def upsert_entry(index: VectorStoreIndex, entry: DocketEntry) -> None:
    """Index a docket entry's text into the vector store.

    Uses entry.id as the document ID so re-indexing the same entry
    replaces the old version rather than creating a duplicate.
    """
    doc = Document(
        text=f"{entry.title}\n\n{entry.content}",
        doc_id=str(entry.id),
        metadata={
            "case_id": str(entry.case_id),
            "court_listener_id": entry.court_listener_id,
            "date_filed": entry.date_filed.isoformat(),
            "type": "docket_entry",
        },
    )
    with contextlib.suppress(Exception):
        index.delete_ref_doc(str(entry.id), delete_from_docstore=True)
    index.insert(doc)
    _save(index)


def upsert_document(
    index: VectorStoreIndex, doc_model: DocketEntryDocument, pdf_path: Path
) -> None:
    """Index all pages of a PDF document into the vector store.

    Each page becomes a separate LlamaIndex Document keyed by
    `<doc_model.id>_page_<n>` for idempotent upserts.

    Requires a PDF reader package such as llama-index-readers-file to be installed.
    """
    try:
        from llama_index.readers.file import PDFReader  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "PDF indexing requires the llama-index-readers-file package. "
            "Install it with: uv add llama-index-readers-file"
        ) from exc

    reader = PDFReader()
    pages = reader.load_data(file=pdf_path)

    for i, page in enumerate(pages):
        doc_id = f"{doc_model.id}_page_{i}"
        page.doc_id = doc_id
        page.metadata.update(
            {
                "docket_entry_id": str(doc_model.docket_entry_id),
                "pdf_url": doc_model.pdf_url,
                "type": "pdf_document",
            }
        )
        with contextlib.suppress(Exception):
            index.delete_ref_doc(doc_id, delete_from_docstore=True)
        index.insert(page)

    _save(index)
