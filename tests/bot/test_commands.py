"""Tests for command handler functions."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from docketmind.commands import ask
from docketmind.platforms import PermissionLevel, PlatformEvent


@pytest.fixture(autouse=True)
async def _db_fixture(in_memory_db):
    """Auto-use the shared in_memory_db fixture for every test in this module."""


def _event(
    command: str = "ask",
    args: dict | None = None,
    user_id: str = "user-1",
    channel_id: str = "guild:chan",
    permission_level: PermissionLevel = PermissionLevel.USER,
) -> PlatformEvent:
    """Build a PlatformEvent with sensible test defaults."""
    return PlatformEvent(
        command=command,
        args=args or {},
        channel_id=channel_id,
        user_id=user_id,
        permission_level=permission_level,
    )


async def test_ask_calls_query_when_no_case_id(monkeypatch):
    """ask() must forward the question to index.query with case_id=None."""
    from docketmind import index

    mock_result = MagicMock()
    mock_result.answer = "The answer."
    mock_result.sources = []
    mock_query = AsyncMock(return_value=mock_result)
    monkeypatch.setattr(index, "query", mock_query)

    response = await ask(_event(args={"question": "What happened?", "case_id": None}))

    mock_query.assert_awaited_once_with("What happened?", case_id=None)
    assert response.text == "The answer."
    assert response.question == "What happened?"


async def test_ask_passes_case_id_to_query(monkeypatch):
    """ask() must forward an explicit case_id through to index.query."""
    from docketmind import index

    mock_result = MagicMock()
    mock_result.answer = "Scoped answer."
    mock_result.sources = []
    mock_query = AsyncMock(return_value=mock_result)
    monkeypatch.setattr(index, "query", mock_query)

    response = await ask(_event(args={"question": "Any updates?", "case_id": "case-abc"}))

    mock_query.assert_awaited_once_with("Any updates?", case_id="case-abc")
    assert response.text == "Scoped answer."
    assert response.question == "Any updates?"
