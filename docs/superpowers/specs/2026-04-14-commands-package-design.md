# Commands Package Design

**Date:** 2026-04-14
**Status:** Approved

## Overview

Restructure the flat `commands.py` module into a `commands/` package where each command
is a class in its own file. A `@command(...)` class decorator in `commands/__init__.py`
captures all metadata (name, description, cooldown, permission), enforces access control
and rate limiting at call time, and auto-registers each command to a global registry on
import. `__main__.py` wires the registry into `Bot` with a single `load(bot)` call.

## Directory Layout

```
docketmind/commands/
    __init__.py      ← Command ABC, @command decorator, registry, load()
    ask.py
    add_case.py
    remove_case.py
    list_cases.py
```

## `commands/__init__.py`

Three public names: `Command`, `command`, `load`.

### `Command` (abstract base class)

```python
class Command(ABC):
    name: str           # stamped by @command
    description: str    # stamped by @command
    cooldown: float     # seconds; 0 means no cooldown
    permission: PermissionLevel

    @abstractmethod
    async def execute(self, event: PlatformEvent) -> BotResponse: ...
```

Metadata fields are class-level attributes set by the decorator, not constructor
arguments. Subclasses declare none of them — the decorator stamps them in.

### `@command(...)` decorator

```python
@command(
    name="ask",
    description="Ask a question about a tracked case",
    cooldown=5.0,
    permission=PermissionLevel.USER,
)
class AskCommand(Command):
    async def execute(self, event: PlatformEvent) -> BotResponse: ...
```

What the decorator does (in order):

1. Stamps `name`, `description`, `cooldown`, `permission` onto the class as class attributes.
2. Wraps `execute` with enforcement logic:
   - **Permission check:** if `event.permission_level < command.permission`, return a
     `BotResponse(text="You don't have permission to use this command.", ephemeral=True)`
     without calling the real `execute`.
   - **Cooldown check:** per-user rate limiting keyed on `(command.name, event.user_id)`.
     If the user is within the cooldown window, return a `BotResponse` with a "slow down"
     message and `ephemeral=True`. No exception is raised — cooldown violations are
     silent ephemeral replies, not errors.
3. Instantiates the class and adds the instance to `_registry[name]`.
4. Returns the class unchanged (so the class object itself is still importable/inspectable).

### `_registry` and `load()`

```python
_registry: dict[str, Command] = {}

def load(bot: Bot) -> None:
    """Register all discovered commands with the bot."""
    for cmd in _registry.values():
        bot.command(cmd.name)(cmd)
```

`load()` iterates `_registry` and calls the existing `bot.command(name)(handler)` API,
so `Bot` itself needs no changes. Because command instances define `execute()`, `Bot`
calls `cmd(event)` — which means `Command` also implements `__call__` as a thin shim:

```python
def __call__(self, event: PlatformEvent) -> Awaitable[BotResponse]:
    return self.execute(event)
```

This preserves the existing `CommandHandler = Callable[[PlatformEvent], Awaitable[BotResponse]]`
type in `Bot` without modification. `__call__` is a concrete instance method on `Command`
itself (not stamped by the decorator) so all subclasses inherit it automatically.

## Individual Command Files

Each file is self-contained. Example:

```python
# commands/ask.py
from docketmind.commands import Command, command
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent

@command(name="ask", description="Ask a question about a tracked case",
         cooldown=5.0, permission=PermissionLevel.USER)
class AskCommand(Command):
    async def execute(self, event: PlatformEvent) -> BotResponse:
        ...
```

No explicit import in `__main__.py` is needed — decorating the class registers it. But
all command modules must be imported for the decorator to run. This is handled by
`commands/__init__.py` importing each submodule at the bottom:

```python
# bottom of commands/__init__.py — triggers registration of all commands
from . import ask, add_case, remove_case, list_cases  # noqa: E402, F401
```

## `__main__.py` changes

Replace the four manual `bot.command("x")(commands.x)` calls with:

```python
import docketmind.commands as commands  # triggers all @command registrations
commands.load(bot)
```

## Cooldown State

Cooldown tracking lives in `commands/__init__.py` in a private dict:

```python
_last_called: dict[tuple[str, str], float] = {}
# key: (command_name, user_id), value: last call timestamp (time.monotonic())
```

No external dependency. Resets on process restart (acceptable for a bot — cooldowns
are rate-limit UX, not security enforcement).

## Error Handling

`execute()` implementations raise exceptions freely. The `Bot.dispatch()` loop is
responsible for catching unhandled exceptions and returning an error `BotResponse`.
Command classes do not catch exceptions internally unless the error is part of expected
business logic (e.g., case not found → return a user-facing message).

## Testing

- `commands/__init__.py`: unit-test the decorator in isolation — verify metadata
  stamping, cooldown rejection, permission rejection, and successful registration.
- Individual command files: test each `execute()` by constructing a `PlatformEvent`
  with appropriate args and mocking external dependencies (`store`, `ingest`, `chat`).
- No change to existing `Bot` tests — `load()` uses the same registration API.

## What Does Not Change

- `Bot`, `bot.py`, `CommandHandler` type — unchanged.
- `platforms/__init__.py` — unchanged.
- All platform adapters — unchanged.
- Alembic migrations, `store.py`, `schedule.py` — unchanged.
