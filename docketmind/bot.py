"""Bot core: command registry, decorators, and the Bot orchestrator."""

import asyncio
import functools
from collections.abc import Awaitable, Callable

from loguru import logger

from docketmind.platforms import BotResponse, PermissionLevel, Platform, PlatformEvent


class PermissionDeniedError(Exception):
    """Raised when a command requires a higher permission level than the caller has."""


class CooldownError(Exception):
    """Raised when a command is invoked before its cooldown expires."""

    def __init__(self, retry_after: float) -> None:
        """Initialise with the number of seconds remaining on the cooldown."""
        self.retry_after = retry_after
        super().__init__(f"Command on cooldown. Retry after {retry_after:.1f}s.")


CommandHandler = Callable[[PlatformEvent], Awaitable[BotResponse]]

# Module-level cooldown state: key → expiry monotonic timestamp
# Maps "handler_name:user_or_channel_id" → monotonic expiry timestamp.
# Uses event-loop time (not wall-clock) so cooldowns are immune to NTP jumps.
_cooldown_state: dict[str, float] = {}


def cooldown(seconds: float, per: str = "user") -> Callable[[CommandHandler], CommandHandler]:
    """Enforce a per-user or per-channel cooldown on a command handler.

    per="user"    — keyed by (command_name, user_id)
    per="channel" — keyed by (command_name, channel_id)

    Raises CooldownError if the handler is called within the cooldown window.
    State is stored in a module-level dict; no external store is needed.
    """

    def decorator(fn: CommandHandler) -> CommandHandler:
        """Wrap fn with cooldown enforcement, preserving its signature."""

        @functools.wraps(fn)
        async def wrapper(event: PlatformEvent) -> BotResponse:
            """Check the cooldown window and delegate to the original handler."""
            key_part = event.user_id if per == "user" else event.channel_id
            key = f"{fn.__name__}:{key_part}"
            now = asyncio.get_running_loop().time()
            expiry = _cooldown_state.get(key, 0.0)
            if now < expiry:
                raise CooldownError(retry_after=expiry - now)
            _cooldown_state[key] = now + seconds
            return await fn(event)

        return wrapper

    return decorator


def requires_permission(level: PermissionLevel) -> Callable[[CommandHandler], CommandHandler]:
    """Raise PermissionDeniedError if the event's permission level is below level."""

    def decorator(fn: CommandHandler) -> CommandHandler:
        """Wrap fn with permission enforcement, preserving its signature."""

        @functools.wraps(fn)
        async def wrapper(event: PlatformEvent) -> BotResponse:
            """Check permission level and delegate to the original handler."""
            if event.permission_level < level:
                raise PermissionDeniedError(
                    f"Command '{fn.__name__}' requires {level.name} permission."
                )
            return await fn(event)

        return wrapper

    return decorator


class Bot:
    """Central orchestrator: registers platforms, routes events to command handlers.

    Usage:
        bot = Bot()

        @bot.platform
        class MyPlatform(Platform): ...

        bot.command("ask")(commands.ask)
        await bot.run()
    """

    def __init__(self) -> None:
        """Initialise with empty platform and handler registries."""
        self._platforms: list[Platform] = []
        self._handlers: dict[str, CommandHandler] = {}

    def platform(self, cls: type[Platform]) -> type[Platform]:
        """Class decorator: instantiate and register a Platform subclass.

        The class is constructed with no arguments; platforms that need config
        read from docketmind.config.settings directly.
        """
        self._platforms.append(cls())
        return cls

    def register_platform(self, instance: Platform) -> None:
        """Register an already-constructed Platform instance."""
        self._platforms.append(instance)

    def command(self, name: str) -> Callable[[CommandHandler], CommandHandler]:
        """Register a coroutine function as the handler for the named command.

        The handler receives a PlatformEvent and must return a BotResponse.
        """

        def decorator(fn: CommandHandler) -> CommandHandler:
            """Store fn as the handler for the command name from the enclosing scope."""
            self._handlers[name] = fn
            return fn

        return decorator

    async def dispatch(self, event: PlatformEvent, platform: Platform) -> None:
        """Look up and invoke the handler for event.command; send result back.

        Sends an ephemeral error BotResponse if no handler is registered,
        or if the handler raises PermissionDeniedError or CooldownError.
        """
        handler = self._handlers.get(event.command)
        if handler is None:
            await platform.send(
                event.channel_id,
                BotResponse(text=f"Unknown command: `{event.command}`.", ephemeral=True),
            )
            return

        try:
            response = await handler(event)
        except PermissionDeniedError:
            response = BotResponse(
                text="You don't have permission to use this command.",
                ephemeral=True,
            )
        except CooldownError as exc:
            response = BotResponse(
                text=f"Slow down! Try again in {exc.retry_after:.1f}s.",
                ephemeral=True,
            )
        except Exception as exc:
            logger.exception("Unhandled error in command '{}': {}", event.command, exc)
            response = BotResponse(
                text="An internal error occurred. Please try again.",
                ephemeral=True,
            )

        await platform.send(event.channel_id, response)

    async def run(self) -> None:
        """Start all registered platforms and drive their event loops concurrently.

        Creates one task per platform via asyncio.TaskGroup. If any task raises
        an unhandled exception, all remaining tasks are cancelled. Platforms are
        disconnected in a finally block.
        """
        try:
            async with asyncio.TaskGroup() as tg:
                for p in self._platforms:
                    await p.connect()
                    tg.create_task(self._run_platform(p))
        finally:
            for p in self._platforms:
                try:
                    await p.disconnect()
                except Exception as exc:
                    logger.warning("Error during platform disconnect: {}", exc)

    async def _run_platform(self, platform: Platform) -> None:
        """Drive a single platform's event loop forever."""
        async for event in platform.events():
            await self.dispatch(event, platform)
