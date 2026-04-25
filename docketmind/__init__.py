"""DocketMind: AI-powered Discord bot for tracking federal lawsuits.

Configures LlamaIndex globals on package import so all submodules share the
same LLM, embedding model, and chunking settings without additional wiring.
"""

from llama_index.core import Settings as LlamaConfig
from llama_index.core.embeddings import BaseEmbedding, MockEmbedding
from llama_index.core.llms import LLM

from docketmind.configure import settings


def _build_llm() -> LLM:
    """Build the LLM from settings."""
    match settings.llm_provider:
        case "mock":
            from llama_index.core.llms.mock import MockLLM  # type: ignore[import-untyped]

            return MockLLM()
        case "openai":
            from llama_index.llms.openai import OpenAI  # type: ignore[import-untyped]

            return OpenAI(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                **settings.llm_extra,
            )
        case "anthropic":
            from llama_index.llms.anthropic import Anthropic  # type: ignore[import-untyped]

            return Anthropic(
                model=settings.llm_model,
                api_key=settings.llm_api_key,
                **settings.llm_extra,
            )


def _build_embed_model() -> BaseEmbedding:
    """Build the embedding model from settings."""
    match settings.embed_provider:
        case "mock":
            return MockEmbedding(embed_dim=1536)
        case "openai":
            from llama_index.embeddings.openai import OpenAIEmbedding

            return OpenAIEmbedding(
                model=settings.embed_model,
                api_key=settings.embed_api_key,
                **settings.embed_extra,
            )


LlamaConfig.llm = _build_llm()
LlamaConfig.embed_model = _build_embed_model()
LlamaConfig.chunk_size = settings.chunk_size
LlamaConfig.chunk_overlap = settings.chunk_overlap
