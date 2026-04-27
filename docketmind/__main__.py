"""Entry point for DocketMind.

Wires up platform adapters and command handlers, starts the ingest
scheduler, then drives the event loop until shutdown.

Run with:
    uv run python -m docketmind
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from loguru import logger

from docketmind.commands import CooldownError, PermissionDeniedError, get_specs
from docketmind.platforms import BotResponse, Platform, PlatformEvent
from docketmind.platforms.discord import DiscordPlatform
from docketmind.schedule import start as ingest_start

if TYPE_CHECKING:
    from docketmind.commands import CommandHandler


async def dispatch(
    event: PlatformEvent,
    platform: Platform,
    handlers: dict[str, CommandHandler],
) -> None:
    """Look up and invoke the handler for *event.command*; send the result back.

    Sends an ephemeral error if no handler is registered, or if the handler
    raises PermissionDeniedError or CooldownError.
    """
    handler = handlers.get(event.command)
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


async def _run_platform(
    platform: Platform,
    handlers: dict[str, CommandHandler],
) -> None:
    """Drive a single platform's event loop forever."""
    async for event in platform.events():
        await dispatch(event, platform, handlers)


async def main() -> None:
    """Bootstrap DocketMind: wire commands, start ingest scheduler, run event loop."""
    specs = get_specs()
    handlers: dict[str, CommandHandler] = {s.name: s.handler for s in specs}

    platform = DiscordPlatform()
    platform.register_commands(specs)

    await ingest_start()

    try:
        async with asyncio.TaskGroup() as tg:
            await platform.connect()
            tg.create_task(_run_platform(platform, handlers))
    finally:
        try:
            await platform.disconnect()
        except Exception as exc:
            logger.warning("Error during platform disconnect: {}", exc)


if __name__ == "__main__":
    asyncio.run(main())
