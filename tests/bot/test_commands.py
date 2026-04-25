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
            court="D. Mass.",
        )
        session.add(case)
        await session.commit()
        await session.refresh(case)
    return case


async def test_ask_calls_query_when_no_case_id(monkeypatch):
    import docketmind.commands.ask as ask_module
    from docketmind.commands.ask import AskCommand

    mock_result = MagicMock()
    mock_result.answer = "The answer."
    mock_result.sources = []
    monkeypatch.setattr(ask_module, "query", AsyncMock(return_value=mock_result))

    cmd = AskCommand()
    response = await cmd.execute(_event(args={"question": "What happened?", "case_id": None}))

    ask_module.query.assert_awaited_once_with("What happened?")
    assert response.text == "The answer."


async def test_ask_uses_chat_engine_when_case_id_provided(monkeypatch):
    import docketmind.commands.ask as ask_module
    from docketmind.commands.ask import AskCommand

    mock_engine = MagicMock()
    mock_engine.achat = AsyncMock(return_value="Answer from engine.")
    monkeypatch.setattr(ask_module, "build_chat_engine", MagicMock(return_value=mock_engine))
    ask_module._chat_engines.clear()

    cmd = AskCommand()
    response = await cmd.execute(_event(args={"question": "Any updates?", "case_id": "case-abc"}))

    ask_module.build_chat_engine.assert_called_once_with("case-abc")
    mock_engine.achat.assert_awaited_once_with("Any updates?")
    assert "Answer from engine." in response.text


async def test_ask_reuses_chat_engine_on_second_call(monkeypatch):
    import docketmind.commands.ask as ask_module
    from docketmind.commands.ask import AskCommand

    mock_engine = MagicMock()
    mock_engine.achat = AsyncMock(return_value="ok")
    mock_build = MagicMock(return_value=mock_engine)
    monkeypatch.setattr(ask_module, "build_chat_engine", mock_build)
    ask_module._chat_engines.clear()

    cmd = AskCommand()
    await cmd.execute(_event(args={"question": "Q1", "case_id": "case-xyz"}, channel_id="ch1"))
    await cmd.execute(_event(args={"question": "Q2", "case_id": "case-xyz"}, channel_id="ch1"))

    assert mock_build.call_count == 1


async def test_ask_evicts_engine_when_case_id_changes(monkeypatch):
    import docketmind.commands.ask as ask_module
    from docketmind.commands.ask import AskCommand

    mock_engine_a = MagicMock()
    mock_engine_a.achat = AsyncMock(return_value="a")
    mock_engine_b = MagicMock()
    mock_engine_b.achat = AsyncMock(return_value="b")
    monkeypatch.setattr(
        ask_module, "build_chat_engine", MagicMock(side_effect=[mock_engine_a, mock_engine_b])
    )
    ask_module._chat_engines.clear()

    cmd = AskCommand()
    await cmd.execute(_event(args={"question": "Q1", "case_id": "case-A"}, channel_id="ch2"))
    await cmd.execute(_event(args={"question": "Q2", "case_id": "case-B"}, channel_id="ch2"))

    assert ask_module.build_chat_engine.call_count == 2
