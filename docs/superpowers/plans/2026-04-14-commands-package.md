# Commands Package Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat `docketmind/commands.py` module with a `commands/` package where each command is a class with an `execute()` method, decorated with `@command(name, description, cooldown, permission)` for auto-registration.

**Architecture:** `commands/__init__.py` owns the `Command` ABC, the `@command` decorator, the global registry, and `load(bot)`. Enforcement (permission check + cooldown) lives in `Command.__call__`, keeping `execute()` as pure business logic. Each command lives in its own file; all are auto-imported at the bottom of `__init__.py`.

**Tech Stack:** Python stdlib `abc`, `time`, `functools`; existing `docketmind.platforms`, `docketmind.bot.Bot`; pytest-asyncio for tests.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `docketmind/commands/__init__.py` | `Command` ABC, `@command` decorator, `_registry`, `_last_called`, `load()` |
| Create | `docketmind/commands/ask.py` | `AskCommand`, module-level `_chat_engines`, `evict_engines_for_case()` |
| Create | `docketmind/commands/add_case.py` | `AddCaseCommand` |
| Create | `docketmind/commands/remove_case.py` | `RemoveCaseCommand` |
| Create | `docketmind/commands/list_cases.py` | `ListCasesCommand` |
| Modify | `docketmind/__main__.py` | Replace 4 manual registrations with `commands.load(bot)` |
| Create | `tests/bot/test_command_decorator.py` | Decorator unit tests |
| Modify | `tests/bot/test_commands.py` | Update to class-based API |
| Delete | `docketmind/commands.py` | Replaced by package |

---

## Task 1: `commands/__init__.py` — Command ABC, decorator, registry

**Files:**
- Create: `docketmind/commands/__init__.py`
- Create: `tests/bot/test_command_decorator.py`

- [ ] **Step 1: Write the failing decorator tests**

Create `tests/bot/test_command_decorator.py`:

```python
"""Tests for the Command ABC and @command decorator."""

import time

import pytest

from docketmind.commands import Command, _last_called, _registry, command
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent


@pytest.fixture(autouse=True)
def clean_state():
    """Clear registry and cooldown state between tests."""
    _registry.clear()
    _last_called.clear()
    yield
    _registry.clear()
    _last_called.clear()


def _event(
    user_id: str = "u1",
    permission_level: PermissionLevel = PermissionLevel.USER,
) -> PlatformEvent:
    return PlatformEvent(
        command="test_cmd",
        args={},
        channel_id="ch1",
        user_id=user_id,
        permission_level=permission_level,
    )


# ---------------------------------------------------------------------------
# Metadata stamping
# ---------------------------------------------------------------------------


def test_command_stamps_metadata_onto_class():
    @command(name="greet", description="Say hi", cooldown=0.0, permission=PermissionLevel.USER)
    class GreetCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="hi")

    assert GreetCommand.name == "greet"
    assert GreetCommand.description == "Say hi"
    assert GreetCommand.cooldown == 0.0
    assert GreetCommand.permission == PermissionLevel.USER


def test_command_registers_instance_in_registry():
    @command(name="greet", description="Say hi", cooldown=0.0, permission=PermissionLevel.USER)
    class GreetCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="hi")

    assert "greet" in _registry
    assert isinstance(_registry["greet"], GreetCommand)


def test_command_returns_the_class_unchanged():
    @command(name="greet", description="Say hi", cooldown=0.0, permission=PermissionLevel.USER)
    class GreetCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="hi")

    # The decorator must return the class, not an instance
    assert isinstance(GreetCommand, type)


# ---------------------------------------------------------------------------
# Permission enforcement (via __call__)
# ---------------------------------------------------------------------------


async def test_call_blocks_user_on_admin_command():
    @command(name="admin_cmd", description="Admin only", cooldown=0.0, permission=PermissionLevel.ADMIN)
    class AdminCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="secret")

    cmd = _registry["admin_cmd"]
    response = await cmd(_event(permission_level=PermissionLevel.USER))

    assert response.ephemeral is True
    assert "permission" in response.text.lower()


async def test_call_allows_admin_on_admin_command():
    @command(name="admin_cmd", description="Admin only", cooldown=0.0, permission=PermissionLevel.ADMIN)
    class AdminCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="secret")

    cmd = _registry["admin_cmd"]
    response = await cmd(_event(permission_level=PermissionLevel.ADMIN))

    assert response.text == "secret"


# ---------------------------------------------------------------------------
# Cooldown enforcement (via __call__)
# ---------------------------------------------------------------------------


async def test_call_allows_first_invocation():
    @command(name="slow_cmd", description="Has cooldown", cooldown=60.0, permission=PermissionLevel.USER)
    class SlowCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="ok")

    cmd = _registry["slow_cmd"]
    response = await cmd(_event(user_id="u1"))
    assert response.text == "ok"


async def test_call_blocks_second_invocation_within_cooldown():
    @command(name="slow_cmd", description="Has cooldown", cooldown=60.0, permission=PermissionLevel.USER)
    class SlowCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="ok")

    cmd = _registry["slow_cmd"]
    await cmd(_event(user_id="u2"))
    response = await cmd(_event(user_id="u2"))

    assert response.ephemeral is True
    assert "slow down" in response.text.lower()


async def test_cooldown_is_per_user():
    @command(name="slow_cmd", description="Has cooldown", cooldown=60.0, permission=PermissionLevel.USER)
    class SlowCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="ok")

    cmd = _registry["slow_cmd"]
    await cmd(_event(user_id="userA"))
    # userB is a different user — must not be blocked
    response = await cmd(_event(user_id="userB"))
    assert response.text == "ok"


async def test_execute_bypasses_enforcement():
    """Calling execute() directly skips permission and cooldown checks."""
    @command(name="admin_cmd", description="Admin only", cooldown=60.0, permission=PermissionLevel.ADMIN)
    class AdminCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="secret")

    cmd = AdminCommand()  # fresh instance, not from registry
    response = await cmd.execute(_event(permission_level=PermissionLevel.USER))
    assert response.text == "secret"


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


def test_load_registers_all_commands_with_bot():
    from unittest.mock import MagicMock

    from docketmind.commands import load

    @command(name="alpha", description="Alpha", cooldown=0.0, permission=PermissionLevel.USER)
    class AlphaCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="alpha")

    @command(name="beta", description="Beta", cooldown=0.0, permission=PermissionLevel.USER)
    class BetaCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="beta")

    bot = MagicMock()
    bot.command.return_value = lambda fn: fn
    load(bot)

    assert bot.command.call_count == 2
    called_names = {call.args[0] for call in bot.command.call_args_list}
    assert called_names == {"alpha", "beta"}
```

- [ ] **Step 2: Run tests — verify they all fail with ImportError**

```bash
uv run pytest tests/bot/test_command_decorator.py -v 2>&1 | head -30
```

Expected: `ImportError: cannot import name 'command' from 'docketmind.commands'` (module doesn't exist yet).

- [ ] **Step 3: Create `docketmind/commands/__init__.py`**

```python
"""Commands package: Command ABC, @command decorator, registry, and load()."""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent

if TYPE_CHECKING:
    from docketmind.bot import Bot

# ---------------------------------------------------------------------------
# Registry and cooldown state
# ---------------------------------------------------------------------------

_registry: dict[str, "Command"] = {}
_last_called: dict[tuple[str, str], float] = {}


# ---------------------------------------------------------------------------
# Command ABC
# ---------------------------------------------------------------------------


class Command(ABC):
    """Abstract base class for all DocketMind commands.

    Subclasses implement execute(). Metadata (name, description, cooldown,
    permission) is stamped onto the class by the @command decorator.

    __call__ enforces permission and cooldown before delegating to execute().
    Tests may call execute() directly to bypass enforcement.
    """

    name: str
    description: str
    cooldown: float
    permission: PermissionLevel

    async def __call__(self, event: PlatformEvent) -> BotResponse:
        """Enforce permission and cooldown, then delegate to execute()."""
        if event.permission_level < self.__class__.permission:
            return BotResponse(
                text="You don't have permission to use this command.",
                ephemeral=True,
            )

        if self.__class__.cooldown > 0:
            key = (self.__class__.name, event.user_id)
            now = time.monotonic()
            expiry = _last_called.get(key, 0.0)
            if now < expiry:
                remaining = expiry - now
                return BotResponse(
                    text=f"Slow down! Try again in {remaining:.1f}s.",
                    ephemeral=True,
                )
            _last_called[key] = now + self.__class__.cooldown

        return await self.execute(event)

    @abstractmethod
    async def execute(self, event: PlatformEvent) -> BotResponse:
        """Execute the command logic. Called after enforcement passes."""
        ...


# ---------------------------------------------------------------------------
# @command decorator
# ---------------------------------------------------------------------------


def command(
    *,
    name: str,
    description: str,
    cooldown: float = 0.0,
    permission: PermissionLevel = PermissionLevel.USER,
) -> Callable[[type[Command]], type[Command]]:
    """Class decorator: stamp metadata, instantiate, and register the command.

    Returns the class unchanged so it remains importable and inspectable.
    The instance is stored in _registry[name].
    """

    def decorator(cls: type[Command]) -> type[Command]:
        cls.name = name
        cls.description = description
        cls.cooldown = cooldown
        cls.permission = permission
        _registry[name] = cls()
        return cls

    return decorator


# ---------------------------------------------------------------------------
# load()
# ---------------------------------------------------------------------------


def load(bot: "Bot") -> None:
    """Register all commands in _registry with the given Bot instance."""
    for cmd in _registry.values():
        bot.command(cmd.name)(cmd)


# ---------------------------------------------------------------------------
# Submodule imports — must come last to avoid circular imports.
# Importing these modules triggers their @command decorators, which populate _registry.
# ---------------------------------------------------------------------------

from docketmind.commands import add_case, ask, list_cases, remove_case  # noqa: E402, F401
```

- [ ] **Step 4: Run the decorator tests**

```bash
uv run pytest tests/bot/test_command_decorator.py -v
```

Expected: All tests pass. Fix any failures before proceeding.

- [ ] **Step 5: Verify type checking passes**

```bash
uv run pyright docketmind/commands/__init__.py
```

Expected: 0 errors. Note: `Callable` type annotation in the `command` function signature uses a string forward reference — if Pyright complains, change the return type to `Callable[[type[Command]], type[Command]]` with a direct import from `collections.abc` at the top of the file.

---

## Task 2: `commands/ask.py` — AskCommand

**Files:**
- Create: `docketmind/commands/ask.py`
- Modify: `tests/bot/test_commands.py` (ask section)

- [ ] **Step 1: Write the failing ask tests**

Replace the entire `tests/bot/test_commands.py` file content (ask section only — keep the DB fixture and helpers, replace the ask tests):

```python
"""Tests for platform-agnostic command handlers."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import docketmind.store as db_module
from docketmind.platforms import PermissionLevel, PlatformEvent
from docketmind.store import Base, Case


# ---------------------------------------------------------------------------
# DB fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def in_memory_db():
    """Wire up an in-memory SQLite DB for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db_module.engine = engine
    db_module.async_session = async_sessionmaker(engine, expire_on_commit=False)
    yield
    await engine.dispose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _event(
    command: str = "ask",
    args: dict | None = None,
    user_id: str = "user-1",
    channel_id: str = "guild:chan",
    permission_level: PermissionLevel = PermissionLevel.USER,
) -> PlatformEvent:
    return PlatformEvent(
        command=command,
        args=args or {},
        channel_id=channel_id,
        user_id=user_id,
        permission_level=permission_level,
    )


async def _insert_case(court_listener_id: str = "12345") -> Case:
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


# ---------------------------------------------------------------------------
# ask
# ---------------------------------------------------------------------------


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
```

- [ ] **Step 2: Run the ask tests — verify they fail with ImportError**

```bash
uv run pytest tests/bot/test_commands.py -k "ask" -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'AskCommand' from 'docketmind.commands.ask'`.

- [ ] **Step 3: Create `docketmind/commands/ask.py`**

```python
"""Ask command: answer questions about tracked cases using RAG."""

from llama_index.core.chat_engine.types import BaseChatEngine

from docketmind.chat import build_chat_engine, query
from docketmind.commands import Command, command
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent

# Per-channel chat engine store: channel_id → (case_id, engine).
# Keyed by channel_id so each channel gets its own conversational context.
# The case_id is stored alongside to detect when it changes.
_chat_engines: dict[str, tuple[str, BaseChatEngine]] = {}


def evict_engines_for_case(case_id: str) -> None:
    """Remove all chat engines associated with case_id."""
    to_remove = [ch for ch, (cid, _) in _chat_engines.items() if cid == case_id]
    for ch in to_remove:
        del _chat_engines[ch]


def _get_or_create_engine(channel_id: str, case_id: str) -> BaseChatEngine:
    """Return the chat engine for channel_id, creating one if case_id changed."""
    entry = _chat_engines.get(channel_id)
    if entry is not None and entry[0] == case_id:
        return entry[1]
    engine = build_chat_engine(case_id)
    _chat_engines[channel_id] = (case_id, engine)
    return engine


@command(name="ask", description="Ask a question about a tracked case", cooldown=5.0, permission=PermissionLevel.USER)
class AskCommand(Command):
    """Answer a question using RAG, optionally scoped to a case.

    Uses a per-channel chat engine for conversational context when case_id is
    provided. Falls back to a global vector search when no case_id is given.

    Expected args: {"question": str, "case_id": str | None}
    """

    async def execute(self, event: PlatformEvent) -> BotResponse:
        """Run the ask logic."""
        question: str = event.args["question"]
        case_id: str | None = event.args.get("case_id")

        if case_id:
            engine = _get_or_create_engine(event.channel_id, case_id)
            llama_response = await engine.achat(question)
            return BotResponse(text=str(llama_response))

        result = await query(question)
        return BotResponse(text=result.answer, citations=result.sources)
```

- [ ] **Step 4: Run the ask tests**

```bash
uv run pytest tests/bot/test_commands.py -k "ask" -v
```

Expected: All 4 ask tests pass.

---

## Task 3: `commands/add_case.py` — AddCaseCommand

**Files:**
- Create: `docketmind/commands/add_case.py`
- Modify: `tests/bot/test_commands.py` (add add_case tests)

- [ ] **Step 1: Append the add_case tests to `tests/bot/test_commands.py`**

Add after the ask tests:

```python
# ---------------------------------------------------------------------------
# add_case
# ---------------------------------------------------------------------------


async def test_add_case_creates_case_and_calls_ingest(monkeypatch):
    import docketmind.commands.add_case as add_case_module
    from docketmind.commands.add_case import AddCaseCommand

    monkeypatch.setattr(
        add_case_module,
        "fetch_case_metadata",
        AsyncMock(return_value=("Smith v. Jones", "D. Mass.")),
    )
    mock_ingest = AsyncMock()
    monkeypatch.setattr(add_case_module, "ingest_add_case", mock_ingest)

    cmd = AddCaseCommand()
    response = await cmd.execute(
        _event(
            command="add_case",
            args={"court_listener_id": "99999"},
            permission_level=PermissionLevel.ADMIN,
        )
    )

    assert "Smith v. Jones" in response.text
    mock_ingest.assert_awaited_once()


async def test_add_case_rejects_duplicate_court_listener_id(monkeypatch):
    import docketmind.commands.add_case as add_case_module
    from docketmind.commands.add_case import AddCaseCommand

    await _insert_case(court_listener_id="12345")

    cmd = AddCaseCommand()
    response = await cmd.execute(
        _event(
            command="add_case",
            args={"court_listener_id": "12345"},
            permission_level=PermissionLevel.ADMIN,
        )
    )

    assert "already" in response.text.lower()
    assert response.ephemeral is True


async def test_add_case_returns_error_when_feed_fails(monkeypatch):
    import docketmind.commands.add_case as add_case_module
    from docketmind.commands.add_case import AddCaseCommand

    monkeypatch.setattr(
        add_case_module,
        "fetch_case_metadata",
        AsyncMock(side_effect=Exception("HTTP error")),
    )

    cmd = AddCaseCommand()
    response = await cmd.execute(
        _event(
            command="add_case",
            args={"court_listener_id": "bad-id"},
            permission_level=PermissionLevel.ADMIN,
        )
    )

    assert response.ephemeral is True
    assert "could not fetch" in response.text.lower()
```

- [ ] **Step 2: Run the add_case tests — verify they fail with ImportError**

```bash
uv run pytest tests/bot/test_commands.py -k "add_case" -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'AddCaseCommand'`.

- [ ] **Step 3: Create `docketmind/commands/add_case.py`**

```python
"""Add-case command: register a new CourtListener case for tracking."""

from loguru import logger

from docketmind import store as db
from docketmind.commands import Command, command
from docketmind.ingest import fetch_case_metadata
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent
from docketmind.schedule import add_case as ingest_add_case
from docketmind.store import Case, get_case_by_court_listener_id as db_get_case


@command(name="add_case", description="Start tracking a CourtListener case", cooldown=0.0, permission=PermissionLevel.ADMIN)
class AddCaseCommand(Command):
    """Register a new case and trigger an immediate backfill sync.

    Fetches case metadata from the CourtListener RSS feed, inserts a Case row,
    then hands off to the ingest scheduler for backfill and polling.

    Expected args: {"court_listener_id": str}
    """

    async def execute(self, event: PlatformEvent) -> BotResponse:
        """Run the add_case logic."""
        court_listener_id: str = event.args["court_listener_id"]

        async with db.async_session() as session:
            if await db_get_case(session, court_listener_id):
                return BotResponse(
                    text=f"Case `{court_listener_id}` is already being tracked.",
                    ephemeral=True,
                )

        rss_url = f"https://www.courtlistener.com/docket/{court_listener_id}/feed/"
        try:
            name, court = await fetch_case_metadata(rss_url)
        except Exception as exc:
            logger.error(f"Failed to fetch metadata for case {court_listener_id}: {exc}")
            return BotResponse(
                text=f"Could not fetch feed for `{court_listener_id}`. Check that the ID is correct.",
                ephemeral=True,
            )

        async with db.async_session() as session:
            case = Case(court_listener_id=court_listener_id, name=name, court=court)
            session.add(case)
            await session.commit()
            await session.refresh(case)

        await ingest_add_case(case.id)
        return BotResponse(text=f"Now tracking **{name}** (`{court_listener_id}`) — {court}.")
```

- [ ] **Step 4: Run the add_case tests**

```bash
uv run pytest tests/bot/test_commands.py -k "add_case" -v
```

Expected: All 3 add_case tests pass.

---

## Task 4: `commands/remove_case.py` — RemoveCaseCommand

**Files:**
- Create: `docketmind/commands/remove_case.py`
- Modify: `tests/bot/test_commands.py` (add remove_case tests)

- [ ] **Step 1: Append the remove_case tests to `tests/bot/test_commands.py`**

Add after the add_case tests:

```python
# ---------------------------------------------------------------------------
# remove_case
# ---------------------------------------------------------------------------


async def test_remove_case_deletes_case_and_calls_scheduler(monkeypatch):
    import docketmind.commands.remove_case as remove_case_module
    from docketmind.commands.remove_case import RemoveCaseCommand

    case = await _insert_case(court_listener_id="55555")
    mock_remove = MagicMock()
    monkeypatch.setattr(remove_case_module, "ingest_remove_case", mock_remove)

    cmd = RemoveCaseCommand()
    response = await cmd.execute(
        _event(
            command="remove_case",
            args={"court_listener_id": "55555"},
            permission_level=PermissionLevel.ADMIN,
        )
    )

    mock_remove.assert_called_once_with(case.id)
    assert response.ephemeral is True
    assert "Stopped tracking" in response.text


async def test_remove_case_returns_error_when_not_found():
    from docketmind.commands.remove_case import RemoveCaseCommand

    cmd = RemoveCaseCommand()
    response = await cmd.execute(
        _event(
            command="remove_case",
            args={"court_listener_id": "unknown"},
            permission_level=PermissionLevel.ADMIN,
        )
    )

    assert "not being tracked" in response.text.lower()
    assert response.ephemeral is True


async def test_remove_case_evicts_chat_engines(monkeypatch):
    import docketmind.commands.ask as ask_module
    import docketmind.commands.remove_case as remove_case_module
    from docketmind.commands.remove_case import RemoveCaseCommand

    case = await _insert_case(court_listener_id="77777")
    ask_module._chat_engines["ch-test"] = (case.id, MagicMock())
    monkeypatch.setattr(remove_case_module, "ingest_remove_case", MagicMock())

    cmd = RemoveCaseCommand()
    await cmd.execute(
        _event(
            command="remove_case",
            args={"court_listener_id": "77777"},
            permission_level=PermissionLevel.ADMIN,
        )
    )

    assert "ch-test" not in ask_module._chat_engines
```

- [ ] **Step 2: Run the remove_case tests — verify they fail with ImportError**

```bash
uv run pytest tests/bot/test_commands.py -k "remove_case" -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'RemoveCaseCommand'`.

- [ ] **Step 3: Create `docketmind/commands/remove_case.py`**

```python
"""Remove-case command: stop tracking a CourtListener case."""

from docketmind import store as db
from docketmind.commands import Command, command
from docketmind.commands.ask import evict_engines_for_case
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent
from docketmind.schedule import remove_case as ingest_remove_case
from docketmind.store import get_case_by_court_listener_id as db_get_case


@command(name="remove_case", description="Stop tracking a CourtListener case", cooldown=0.0, permission=PermissionLevel.ADMIN)
class RemoveCaseCommand(Command):
    """Remove a tracked case and its scheduled sync job.

    Deletes the Case row (cascade-deletes entries and documents) and removes
    the polling job from the scheduler. Evicts any chat engines for this case.

    Expected args: {"court_listener_id": str}
    """

    async def execute(self, event: PlatformEvent) -> BotResponse:
        """Run the remove_case logic."""
        court_listener_id: str = event.args["court_listener_id"]

        async with db.async_session() as session:
            case = await db_get_case(session, court_listener_id)
            if case is None:
                return BotResponse(
                    text=f"Case `{court_listener_id}` is not being tracked.",
                    ephemeral=True,
                )
            case_id = case.id
            name = case.name
            await session.delete(case)
            await session.commit()

        ingest_remove_case(case_id)
        evict_engines_for_case(case_id)
        return BotResponse(
            text=f"Stopped tracking **{name}** (`{court_listener_id}`).",
            ephemeral=True,
        )
```

- [ ] **Step 4: Run the remove_case tests**

```bash
uv run pytest tests/bot/test_commands.py -k "remove_case" -v
```

Expected: All 3 remove_case tests pass.

---

## Task 5: `commands/list_cases.py` — ListCasesCommand

**Files:**
- Create: `docketmind/commands/list_cases.py`
- Modify: `tests/bot/test_commands.py` (add list_cases tests)

- [ ] **Step 1: Append the list_cases tests to `tests/bot/test_commands.py`**

Add after the remove_case tests:

```python
# ---------------------------------------------------------------------------
# list_cases
# ---------------------------------------------------------------------------


async def test_list_cases_returns_formatted_list():
    from docketmind.commands.list_cases import ListCasesCommand

    await _insert_case(court_listener_id="AAA")
    await _insert_case(court_listener_id="BBB")

    cmd = ListCasesCommand()
    response = await cmd.execute(_event(command="list_cases"))

    assert "Smith v. Jones" in response.text
    assert "AAA" in response.text
    assert "BBB" in response.text


async def test_list_cases_returns_empty_message_when_no_cases():
    from docketmind.commands.list_cases import ListCasesCommand

    cmd = ListCasesCommand()
    response = await cmd.execute(_event(command="list_cases"))

    assert "No cases" in response.text
```

- [ ] **Step 2: Run the list_cases tests — verify they fail with ImportError**

```bash
uv run pytest tests/bot/test_commands.py -k "list_cases" -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'ListCasesCommand'`.

- [ ] **Step 3: Create `docketmind/commands/list_cases.py`**

```python
"""List-cases command: display all currently tracked cases."""

from datetime import datetime

from docketmind import store as db
from docketmind.commands import Command, command
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent
from docketmind.store import list_cases as db_list_cases


def _fmt_time(dt: datetime | None) -> str:
    """Format a datetime as a human-readable string, or 'never' if None."""
    return dt.strftime("%Y-%m-%d %H:%M UTC") if dt else "never"


@command(name="list_cases", description="List all currently tracked cases", cooldown=0.0, permission=PermissionLevel.USER)
class ListCasesCommand(Command):
    """List all currently tracked cases with their last-synced time.

    No args required.
    """

    async def execute(self, event: PlatformEvent) -> BotResponse:
        """Run the list_cases logic."""
        async with db.async_session() as session:
            cases = await db_list_cases(session)

        if not cases:
            return BotResponse(text="No cases are currently being tracked.")

        lines = [
            f"**{c.name}** (`{c.court_listener_id}`) — {c.court} — last synced: {_fmt_time(c.last_synced_at)}"
            for c in cases
        ]
        return BotResponse(text="**Tracked Cases:**\n" + "\n".join(lines))
```

- [ ] **Step 4: Run the list_cases tests**

```bash
uv run pytest tests/bot/test_commands.py -k "list_cases" -v
```

Expected: Both list_cases tests pass.

---

## Task 6: Wire submodule imports in `commands/__init__.py`

**Files:**
- Modify: `docketmind/commands/__init__.py` (add submodule imports at the bottom)

The `__init__.py` created in Task 1 already has the submodule imports at the bottom:

```python
from docketmind.commands import add_case, ask, list_cases, remove_case  # noqa: E402, F401
```

- [ ] **Step 1: Verify the import line is present**

```bash
uv run python -c "import docketmind.commands; print(list(docketmind.commands._registry.keys()))"
```

Expected output: `['ask', 'add_case', 'remove_case', 'list_cases']` (order may vary).

If the output shows an empty list or missing commands, check that the bottom of `commands/__init__.py` has:

```python
from docketmind.commands import add_case, ask, list_cases, remove_case  # noqa: E402, F401
```

- [ ] **Step 2: Run the full test suite for commands**

```bash
uv run pytest tests/bot/ -v
```

Expected: All tests in `test_bot.py`, `test_command_decorator.py`, and `test_commands.py` pass.

---

## Task 7: Update `__main__.py` and delete `commands.py`

**Files:**
- Modify: `docketmind/__main__.py`
- Delete: `docketmind/commands.py`

- [ ] **Step 1: Update `docketmind/__main__.py`**

Replace the file content:

```python
"""Entry point for DocketMind.

Wires up the Bot, registers platform adapters and command handlers, starts
the ingest scheduler, then runs the bot until shutdown.

Run with:
    uv run python -m docketmind
"""

import asyncio

import docketmind  # noqa: F401 — configures LlamaIndex globals before anything else
import docketmind.commands as commands
from docketmind.bot import Bot
from docketmind.platforms.discord import DiscordPlatform
from docketmind.schedule import start as ingest_start


async def main() -> None:
    """Bootstrap DocketMind: wire commands, start ingest scheduler, run bot."""
    bot = Bot()

    # Register the Discord platform adapter
    bot.register_platform(DiscordPlatform())

    # Register all command handlers from the commands package
    commands.load(bot)

    # Start ingest scheduler (registers APScheduler jobs for all existing cases)
    await ingest_start()

    # Run bot — blocks until shutdown (KeyboardInterrupt or unhandled error)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Delete `docketmind/commands.py`**

```bash
rm docketmind/commands.py
```

- [ ] **Step 3: Run the full test suite**

```bash
uv run pytest -v
```

Expected: All tests pass. If any test still imports `from docketmind import commands` expecting module-level attributes like `commands.ask`, it will fail — fix by updating those references to import from the submodule (e.g., `from docketmind.commands.ask import AskCommand`).

- [ ] **Step 4: Run linting and type checking**

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

Expected: 0 errors, 0 warnings. Fix any issues before finishing.
