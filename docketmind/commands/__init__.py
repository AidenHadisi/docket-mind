"""Commands package: Command ABC, @command decorator, registry, and load()."""

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent

if TYPE_CHECKING:
    from docketmind.bot import Bot

_registry: dict[str, "Command"] = {}
_last_called: dict[tuple[str, str], float] = {}


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
        """Stamp metadata onto cls, instantiate it into the registry, and return cls."""
        cls.name = name
        cls.description = description
        cls.cooldown = cooldown
        cls.permission = permission
        _registry[name] = cls()
        return cls

    return decorator


def load(bot: "Bot") -> None:
    """Register all commands in _registry with the given Bot instance."""
    for cmd in _registry.values():
        bot.command(cmd.name)(cmd)


# Submodule imports must come last to avoid circular imports.
# Importing these modules triggers their @command decorators, which populate _registry.
# from docketmind.commands import add_case, ask, list_cases, remove_case  # noqa: E402, F401
