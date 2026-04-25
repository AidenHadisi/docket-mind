"""Entry point for DocketMind.

Wires up the Bot, registers platform adapters and command handlers, starts
the ingest scheduler, then runs the bot until shutdown.

Run with:
    uv run python -m docketmind
"""

import asyncio

import docketmind  # noqa: F401 — configures LlamaIndex globals before anything else
from docketmind.bot import Bot
from docketmind.commands import get_specs
from docketmind.platforms.discord import DiscordPlatform
from docketmind.schedule import start as ingest_start


async def main() -> None:
    """Bootstrap DocketMind: wire commands, start ingest scheduler, run bot."""
    bot = Bot()
    specs = get_specs()

    platform = DiscordPlatform()
    platform.register_commands(specs)
    bot.register_platform(platform)

    bot.register_commands(specs)

    await ingest_start()

    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
