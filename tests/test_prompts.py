"""Tests for system prompts and prompt templates."""

from unittest.mock import AsyncMock, MagicMock

from llama_index.core.llms import MessageRole

from docketmind.prompts import (
    BASE_SYSTEM_PROMPT,
    DOCKET_QA_TEMPLATE,
    DOCKET_REFINE_TEMPLATE,
)


def test_base_prompt_anchors_persona():
    """The base prompt names the assistant and the domain so SummaryExtractor
    calls (which bypass the QA template) still inherit the persona."""
    assert "DocketMind" in BASE_SYSTEM_PROMPT
    assert "U.S. federal" in BASE_SYSTEM_PROMPT


def test_qa_template_has_system_then_user():
    msgs = DOCKET_QA_TEMPLATE.format_messages(context_str="CTX", query_str="Q?")
    assert len(msgs) == 2
    assert msgs[0].role == MessageRole.SYSTEM
    assert msgs[1].role == MessageRole.USER


def test_qa_template_renders_substitutions():
    msgs = DOCKET_QA_TEMPLATE.format_messages(
        context_str="The court granted X.",
        query_str="What did the court decide?",
    )
    user_content = msgs[1].content or ""
    assert "The court granted X." in user_content
    assert "What did the court decide?" in user_content


def test_qa_template_encodes_no_advice_and_no_inline_citations():
    """Critical guardrails: no personal advice, no bracketed inline citations."""
    system_content = (DOCKET_QA_TEMPLATE.message_templates[0].content or "").lower()
    assert "do not give personal legal advice" in system_content
    assert "do not output bracketed inline citations" in system_content


def test_qa_template_demands_grounding():
    """The bot must refuse to guess when the excerpts do not answer the question."""
    system_content = DOCKET_QA_TEMPLATE.message_templates[0].content or ""
    assert "Use ONLY these excerpts" in system_content
    assert "do not fall back on general knowledge" in system_content.lower()


def test_refine_template_renders_all_placeholders():
    msgs = DOCKET_REFINE_TEMPLATE.format_messages(
        query_str="Q?",
        existing_answer="EXISTING",
        context_msg="NEW",
    )
    assert len(msgs) == 2
    assert msgs[0].role == MessageRole.SYSTEM
    user_content = msgs[1].content or ""
    assert "Q?" in user_content
    assert "EXISTING" in user_content
    assert "NEW" in user_content


def test_refine_template_preserves_persona():
    system_content = DOCKET_REFINE_TEMPLATE.message_templates[0].content or ""
    assert "DocketMind" in system_content
    assert "no personal advice" in system_content.lower()


async def test_query_passes_templates_to_engine(monkeypatch):
    """index.query must forward our templates to RetrieverQueryEngine so the
    default LlamaIndex prompts never reach the LLM in production."""
    from docketmind import index

    fake_response = MagicMock()
    fake_response.source_nodes = []
    fake_response.__str__ = MagicMock(return_value="stubbed answer")

    fake_engine = MagicMock()
    fake_engine.aquery = AsyncMock(return_value=fake_response)

    fake_retriever = MagicMock()
    monkeypatch.setattr(index, "_build_retriever", MagicMock(return_value=fake_retriever))

    from_args = MagicMock(return_value=fake_engine)
    monkeypatch.setattr(index.RetrieverQueryEngine, "from_args", from_args)

    result = await index.query("any question")

    from_args.assert_called_once()
    kwargs = from_args.call_args.kwargs
    assert kwargs["text_qa_template"] is DOCKET_QA_TEMPLATE
    assert kwargs["refine_template"] is DOCKET_REFINE_TEMPLATE
    assert result.answer == "stubbed answer"
