"""Tests for per-case memory updater."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

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


async def test_update_case_memory_returns_string(case_no_memory, new_entries):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "Updated summary text."

    with patch("docketmind.ingestion.memory._client") as mock_client:
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        result = await update_case_memory(case_no_memory, new_entries)

    assert result == "Updated summary text."


async def test_update_case_memory_includes_existing_memory_in_prompt(case_with_memory, new_entries):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "New summary."

    with patch("docketmind.ingestion.memory._client") as mock_client:
        create_mock = AsyncMock(return_value=mock_response)
        mock_client.chat.completions.create = create_mock
        await update_case_memory(case_with_memory, new_entries)

    prompt = create_mock.call_args[1]["messages"][0]["content"]
    assert "Prior summary: case involves tax fraud allegations." in prompt


async def test_update_case_memory_includes_new_entries_in_prompt(case_no_memory, new_entries):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = "New summary."

    with patch("docketmind.ingestion.memory._client") as mock_client:
        create_mock = AsyncMock(return_value=mock_response)
        mock_client.chat.completions.create = create_mock
        await update_case_memory(case_no_memory, new_entries)

    prompt = create_mock.call_args[1]["messages"][0]["content"]
    assert "Order GRANTING Motion to Dismiss" in prompt
