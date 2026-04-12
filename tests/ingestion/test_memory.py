"""Tests for per-case memory updater."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from docketmind.ingestion.memory import update_case_memory
from docketmind.models import Case, DocketEntry


@pytest.fixture
def case_no_memory() -> Case:
    return Case(
        id="case-001",
        court_listener_id="12345",
        name="United States v. Doe",
        court="D. Mass.",
        memory_text=None,
    )


@pytest.fixture
def case_with_memory() -> Case:
    return Case(
        id="case-001",
        court_listener_id="12345",
        name="United States v. Doe",
        court="D. Mass.",
        memory_text="Prior summary: case involves tax fraud allegations.",
    )


@pytest.fixture
def new_entries() -> list[DocketEntry]:
    return [
        DocketEntry(
            id="entry-001",
            case_id="case-001",
            court_listener_id="cl-001",
            title="Order GRANTING Motion to Dismiss",
            content="Court grants motion to dismiss counts 1-3.",
            content_hash="abc",
            date_filed=datetime(2026, 4, 7, tzinfo=UTC),
        )
    ]


@pytest.fixture
def mock_openai(mocker) -> AsyncMock:
    """Patch the OpenAI client and return the completions.create mock."""
    mock_client = mocker.patch("docketmind.ingestion.memory._client")
    create_mock = AsyncMock()
    mock_client.chat.completions.create = create_mock
    return create_mock


def _make_response(content: str) -> MagicMock:
    response = MagicMock()
    response.choices[0].message.content = content
    return response


async def test_update_case_memory_returns_string(mock_openai, case_no_memory, new_entries):
    mock_openai.return_value = _make_response("Updated summary text.")

    result = await update_case_memory(case_no_memory, new_entries)

    assert result == "Updated summary text."


async def test_update_case_memory_includes_existing_memory_in_prompt(
    mock_openai, case_with_memory, new_entries
):
    mock_openai.return_value = _make_response("New summary.")

    await update_case_memory(case_with_memory, new_entries)

    prompt = mock_openai.call_args[1]["messages"][0]["content"]
    assert "Prior summary: case involves tax fraud allegations." in prompt


async def test_update_case_memory_includes_new_entries_in_prompt(
    mock_openai, case_no_memory, new_entries
):
    mock_openai.return_value = _make_response("New summary.")

    await update_case_memory(case_no_memory, new_entries)

    prompt = mock_openai.call_args[1]["messages"][0]["content"]
    assert "Order GRANTING Motion to Dismiss" in prompt
