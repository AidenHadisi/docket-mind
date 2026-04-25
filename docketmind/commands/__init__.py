"""Commands package: @command decorator, CommandSpec, and auto-registration.

Decorate handler functions with @command(...) to declare metadata, enforce
permissions and cooldowns, and auto-register into the global spec list.
Platform adapters and the Bot orchestrator consume get_specs() to wire themselves.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import NamedTuple

from cachetools import TTLCache

from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent

type CommandHandler = Callable[[PlatformEvent], Awaitable[BotResponse]]

_registry: list[CommandSpec] = []


class CommandParam(NamedTuple):
    """One parameter accepted by a bot command."""

    name: str
    type: type
    description: str
    required: bool = True


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Declarative definition of a bot command.

    Consumed by Bot.register_commands() (maps name -> handler) and by each
    Platform.register_commands() (builds native UI elements from the metadata).
    """

    name: str
    description: str
    handler: CommandHandler
    params: list[CommandParam] = field(default_factory=list)
    cooldown: float = 0.0
    permission: PermissionLevel = PermissionLevel.USER
    ephemeral_defer: bool = False


class PermissionDeniedError(Exception):
    """Raised when a command requires a higher permission level than the caller has."""


class CooldownError(Exception):
    """Raised when a command is invoked before its cooldown expires."""

    def __init__(self, retry_after: float) -> None:
        """Initialise with the number of seconds remaining on the cooldown."""
        self.retry_after = retry_after
        super().__init__(f"Command on cooldown. Retry after {retry_after:.1f}s.")


def command(
    *,
    name: str,
    description: str,
    params: list[CommandParam] | None = None,
    cooldown: float = 0.0,
    permission: PermissionLevel = PermissionLevel.USER,
    ephemeral_defer: bool = False,
) -> Callable[[CommandHandler], CommandHandler]:
    """Decorator: declare metadata, wrap with enforcement, and register the handler.

    Applies permission checking (if permission > USER) and cooldown enforcement
    (if cooldown > 0) automatically, then stores a CommandSpec in the global
    registry. The decorated function gains a ``__command_spec__`` attribute.
    """

    def decorator(fn: CommandHandler) -> CommandHandler:
        inner = fn

        if permission > PermissionLevel.USER:
            _inner_perm = inner

            @functools.wraps(_inner_perm)
            async def _perm_wrapper(event: PlatformEvent) -> BotResponse:
                if event.permission_level < permission:
                    raise PermissionDeniedError(
                        f"Command '{name}' requires {permission.name} permission."
                    )
                return await _inner_perm(event)

            inner = _perm_wrapper

        if cooldown > 0:
            _inner = inner  # capture for closure
            _cd_cache: TTLCache[str, bool] = TTLCache(maxsize=4096, ttl=cooldown)

            @functools.wraps(_inner)
            async def _cd_wrapper(event: PlatformEvent) -> BotResponse:
                key = event.user_id
                if key in _cd_cache:
                    raise CooldownError(retry_after=cooldown)
                result = await _inner(event)
                _cd_cache[key] = True
                return result

            inner = _cd_wrapper

        spec = CommandSpec(
            name=name,
            description=description,
            handler=inner,
            params=params or [],
            cooldown=cooldown,
            permission=permission,
            ephemeral_defer=ephemeral_defer,
        )
        inner.__command_spec__ = spec  # type: ignore[attr-defined]
        fn.__command_spec__ = spec  # type: ignore[attr-defined]
        _registry.append(spec)
        return inner

    return decorator


def get_specs() -> list[CommandSpec]:
    """Return a snapshot of all registered command specs."""
    return list(_registry)


# Submodule imports trigger @command decorators, populating _registry.
import docketmind.commands.ask  # noqa: E402, F401
import docketmind.commands.cases  # noqa: E402, F401
