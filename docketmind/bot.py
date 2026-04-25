"""Bot core: the Bot orchestrator that routes platform events to command handlers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from docketmind.commands import CooldownError, PermissionDeniedError
from docketmind.platforms import BotResponse, Platform, PlatformEvent

if TYPE_CHECKING:
    from docketmind.commands import CommandHandler, CommandSpec


class Bot:
    """Central orchestrator: registers platforms, routes events to command handlers.

    Usage:
        from docketmind.commands import get_specs

        bot = Bot()
        bot.register_commands(get_specs())
        bot.register_platform(discord_platform)
        await bot.run()
    """

    def __init__(self) -> None:
        """Initialise with empty platform and handler registries."""
        self._platforms: list[Platform] = []
        self._handlers: dict[str, CommandHandler] = {}

    def register_platform(self, instance: Platform) -> None:
        """Register an already-constructed Platform instance."""
        self._platforms.append(instance)

    def register_commands(self, specs: list[CommandSpec]) -> None:
        """Bulk-register command handlers from a list of CommandSpec definitions."""
        for spec in specs:
            self._handlers[spec.name] = spec.handler

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
