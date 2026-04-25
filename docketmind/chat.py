"""RAG query engine and chat engine for answering questions about cases."""

from llama_index.core.chat_engine.types import BaseChatEngine, ChatMode
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters
from pydantic import BaseModel

from docketmind.configure import settings
from docketmind.index import index


class SourceChunk(BaseModel):
    """A single retrieved chunk used to answer a question."""

    text: str
    score: float
    type: str
    case_id: str | None = None
    court_listener_id: str | None = None
    date_filed: str | None = None
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

    engine = index.as_query_engine(filters=filters, similarity_top_k=settings.similarity_top_k)
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


def build_chat_engine(case_id: str) -> BaseChatEngine:
    """Build a stateful chat engine scoped to a single case.

    Uses condense_plus_context mode: conversation history is condensed into a
    standalone query each turn, then fresh context is retrieved from the index.
    The returned engine holds its own ChatMemoryBuffer — callers should keep one
    instance per conversation (e.g. per Discord channel) and reuse it across turns.
    """
    filters = MetadataFilters(filters=[MetadataFilter(key="case_id", value=case_id)])
    return index.as_chat_engine(
        chat_mode=ChatMode.CONDENSE_PLUS_CONTEXT,
        filters=filters,
    )
