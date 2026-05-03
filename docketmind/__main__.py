"""Entry point for DocketMind.

Wires up platform adapters and command handlers, starts the ingest
scheduler, then drives the event loop until shutdown.

Run with:
    uv run python -m docketmind
"""

from __future__ import annotations

import asyncio

from loguru import logger

from docketmind.commands import COMMANDS, CommandSpec, PermissionDeniedError
from docketmind.cooldown import CooldownError, tracker
from docketmind.platforms import BotResponse, Platform, PlatformEvent, create_platforms
from docketmind.schedule import start as ingest_start


async def dispatch(
    event: PlatformEvent,
    platform: Platform,
    specs: dict[str, CommandSpec],
) -> None:
    """Look up and invoke the handler for *event.command*; send the result back.

    Checks permission and cooldown before calling the handler. Sends an
    ephemeral error for unknown commands, permission denials, or cooldowns.
    """
    spec = specs.get(event.command)
    if spec is None:
        await platform.send(
            event.channel_id,
            BotResponse(text=f"Unknown command: `{event.command}`.", ephemeral=True),
        )
        return

    try:
        if spec.permission > event.permission_level:
            raise PermissionDeniedError
        await tracker.hit(spec, event, platform.name)
        response = await spec.handler(event)
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
    specs: dict[str, CommandSpec],
) -> None:
    """Drive a single platform's event loop forever."""
    async for event in platform.events():
        await dispatch(event, platform, specs)


async def main() -> None:
    """Bootstrap DocketMind: wire commands, start ingest scheduler, run event loop."""
    specs: dict[str, CommandSpec] = {s.name: s for s in COMMANDS}

    platforms = create_platforms()
    for p in platforms:
        p.register_commands(COMMANDS)

    if not platforms:
        logger.error("No platforms configured — set at least one platform token.")
        return

    await ingest_start()

    try:
        async with asyncio.TaskGroup() as tg:
            for p in platforms:
                await p.connect()
                tg.create_task(_run_platform(p, specs))
    finally:
        for p in platforms:
            try:
                await p.disconnect()
            except Exception as exc:
                logger.warning("Error during platform disconnect: {}", exc)


if __name__ == "__main__":
    asyncio.run(main())
