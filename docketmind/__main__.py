"""Entry point for DocketMind.

Wires up the Bot, registers platform adapters and command handlers, starts
the ingest scheduler, then runs the bot until shutdown.

Run with:
    uv run python -m docketmind
"""

import asyncio

import docketmind  # noqa: F401 — configures LlamaIndex globals before anything else
from docketmind import commands
from docketmind.bot import Bot
from docketmind.platforms.discord import DiscordPlatform
from docketmind.schedule import start as ingest_start


async def main() -> None:
    """Bootstrap DocketMind: wire commands, start ingest scheduler, run bot."""
    bot = Bot()

    # Register the Discord platform adapter
    bot.register_platform(DiscordPlatform())

    # Register all command handlers
    bot.command("ask")(commands.ask)
    bot.command("add_case")(commands.add_case)
    bot.command("remove_case")(commands.remove_case)
    bot.command("list_cases")(commands.list_cases)

    # Start ingest scheduler (registers APScheduler jobs for all existing cases)
    await ingest_start()

    # Run bot — blocks until shutdown (KeyboardInterrupt or unhandled error)
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
