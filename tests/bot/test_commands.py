"""Tests for platform-agnostic command handlers."""

from unittest.mock import AsyncMock, MagicMock

import pytest

import docketmind.store as db_module
from docketmind.platforms import PermissionLevel, PlatformEvent
from docketmind.store import Case


@pytest.fixture(autouse=True)
async def _db(in_memory_db):
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


async def _insert_case(court_listener_id: str = "12345") -> Case:
    """Persist a Case to the in-memory DB and return the refreshed instance."""
    async with db_module.async_session() as session:
        case = Case(
            court_listener_id=court_listener_id,
            name="Smith v. Jones",
        )
        session.add(case)
        await session.commit()
        await session.refresh(case)
    return case


async def test_ask_calls_query_when_no_case_id(monkeypatch):
    import docketmind.commands.ask as ask_module
    from docketmind.commands.ask import ask

    mock_result = MagicMock()
    mock_result.answer = "The answer."
    mock_result.sources = []
    monkeypatch.setattr(ask_module, "query", AsyncMock(return_value=mock_result))

    response = await ask.__wrapped__(_event(args={"question": "What happened?", "case_id": None}))  # type: ignore[attr-defined]

    ask_module.query.assert_awaited_once_with("What happened?", case_id=None)  # type: ignore[attr-defined]
    assert response.text == "The answer."


async def test_ask_passes_case_id_to_query(monkeypatch):
    import docketmind.commands.ask as ask_module
    from docketmind.commands.ask import ask

    mock_result = MagicMock()
    mock_result.answer = "Scoped answer."
    mock_result.sources = []
    monkeypatch.setattr(ask_module, "query", AsyncMock(return_value=mock_result))

    response = await ask.__wrapped__(  # type: ignore[attr-defined]
        _event(args={"question": "Any updates?", "case_id": "case-abc"})
    )

    ask_module.query.assert_awaited_once_with("Any updates?", case_id="case-abc")  # type: ignore[attr-defined]
    assert response.text == "Scoped answer."
