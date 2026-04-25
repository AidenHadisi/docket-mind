"""Discord platform adapter using discord.py 2.x slash commands."""

import asyncio
from collections.abc import AsyncIterator

import discord
from discord import app_commands
from discord.ext import commands as ext_commands
from loguru import logger

from docketmind.configure import settings
from docketmind.platforms import BotResponse, PermissionLevel, Platform, PlatformEvent

# Maximum characters in a Discord message
_DISCORD_MAX_LENGTH = 2000
# Reserve room for citations; truncate answer text at this limit
_ANSWER_MAX_LENGTH = 1800


def _format_response(response: BotResponse) -> str:
    """Render a BotResponse as a Discord message string.

    Truncates the answer text to _ANSWER_MAX_LENGTH and appends up to 5
    source citations. The combined output never exceeds _DISCORD_MAX_LENGTH.
    """
    text = response.text[:_ANSWER_MAX_LENGTH]
    if response.citations:
        citation_lines: list[str] = []
        for i, src in enumerate(response.citations[:5], start=1):
            date = src.date_filed or "unknown date"
            url = src.pdf_url or ""
            citation_lines.append(f"[{i}] {date} — {url}" if url else f"[{i}] {date}")
        text += "\n\n**Sources:**\n" + "\n".join(citation_lines)
    return text[:_DISCORD_MAX_LENGTH]


class DiscordPlatform(Platform):
    """Maps discord.py slash command interactions to PlatformEvent/BotResponse.

    Slash commands are registered on a specific guild (settings.discord_guild_id)
    for instant propagation during development, or globally when the setting is None.

    Permission mapping:
        interaction.user.guild_permissions.administrator → PermissionLevel.ADMIN
        all others                                        → PermissionLevel.USER

    channel_id encoding:
        f"{interaction.guild_id}:{interaction.channel_id}"
        Prevents cross-guild collisions on the shared chat engine store.

    Deferred response pattern:
        Each slash command handler calls interaction.response.defer() immediately
        (before any async work) to avoid Discord's 3-second acknowledgement timeout.
        The interaction is stored in self._pending[channel_id]. After Bot.dispatch()
        resolves, send() pops the interaction and calls followup.send().
    """

    def __init__(self) -> None:
        """Initialise the Discord client, command tree, and internal queues."""
        intents = discord.Intents.default()
        self._client: ext_commands.Bot = ext_commands.Bot(
            command_prefix="!",  # unused but required by ext.commands.Bot
            intents=intents,
        )
        self._tree: app_commands.CommandTree = self._client.tree
        self._event_queue: asyncio.Queue[PlatformEvent] = asyncio.Queue()
        # Stash the Interaction so send() can reply via followup after dispatch completes
        self._pending: dict[str, discord.Interaction] = {}
        self._ready = asyncio.Event()
        self._register_commands()
        self._register_events()

    def _channel_id(self, interaction: discord.Interaction) -> str:
        """Build an opaque channel_id string from guild and channel IDs."""
        return f"{interaction.guild_id}:{interaction.channel_id}"

    def _permission_level(self, interaction: discord.Interaction) -> PermissionLevel:
        """Map a Discord interaction's permission flags to an abstract PermissionLevel."""
        if (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        ):
            return PermissionLevel.ADMIN
        return PermissionLevel.USER

    def _register_events(self) -> None:
        """Attach discord.py lifecycle event handlers."""

        @self._client.event
        async def on_ready() -> None:
            """Sync the command tree once the client is connected."""
            if settings.discord_guild_id:
                guild = discord.Object(id=settings.discord_guild_id)
                self._tree.copy_global_to(guild)  # type: ignore[arg-type]
                await self._tree.sync(guild=guild)
                logger.info("Discord adapter ready (guild sync, id={})", settings.discord_guild_id)
            else:
                await self._tree.sync()
                logger.info("Discord adapter ready (global sync)")
            self._ready.set()

    def _register_commands(self) -> None:
        """Attach slash command callbacks to the CommandTree."""

        @self._tree.command(name="ask", description="Ask a question about a tracked case")
        @app_commands.describe(
            question="The question to ask",
            case_id="Optional: scope the question to a specific case ID",
        )
        async def _ask(
            interaction: discord.Interaction,
            question: str,
            case_id: str | None = None,
        ) -> None:
            """Defer the response and enqueue an ask event for Bot.dispatch."""
            # Defer immediately to avoid Discord's 3-second ack timeout
            await interaction.response.defer()
            channel_id = self._channel_id(interaction)
            self._pending[channel_id] = interaction
            await self._event_queue.put(
                PlatformEvent(
                    command="ask",
                    args={"question": question, "case_id": case_id},
                    channel_id=channel_id,
                    user_id=str(interaction.user.id),
                    permission_level=self._permission_level(interaction),
                    raw=interaction,
                )
            )

        @self._tree.command(name="add_case", description="Start tracking a CourtListener case")
        @app_commands.describe(court_listener_id="CourtListener docket ID (numeric)")
        async def _add_case(
            interaction: discord.Interaction,
            court_listener_id: str,
        ) -> None:
            """Defer the response and enqueue an add_case event for Bot.dispatch."""
            await interaction.response.defer(ephemeral=True)
            channel_id = self._channel_id(interaction)
            self._pending[channel_id] = interaction
            await self._event_queue.put(
                PlatformEvent(
                    command="add_case",
                    args={"court_listener_id": court_listener_id},
                    channel_id=channel_id,
                    user_id=str(interaction.user.id),
                    permission_level=self._permission_level(interaction),
                    raw=interaction,
                )
            )

        @self._tree.command(name="remove_case", description="Stop tracking a CourtListener case")
        @app_commands.describe(court_listener_id="CourtListener docket ID to remove")
        async def _remove_case(
            interaction: discord.Interaction,
            court_listener_id: str,
        ) -> None:
            """Defer the response and enqueue a remove_case event for Bot.dispatch."""
            await interaction.response.defer(ephemeral=True)
            channel_id = self._channel_id(interaction)
            self._pending[channel_id] = interaction
            await self._event_queue.put(
                PlatformEvent(
                    command="remove_case",
                    args={"court_listener_id": court_listener_id},
                    channel_id=channel_id,
                    user_id=str(interaction.user.id),
                    permission_level=self._permission_level(interaction),
                    raw=interaction,
                )
            )

        @self._tree.command(name="list_cases", description="List all currently tracked cases")
        async def _list_cases(interaction: discord.Interaction) -> None:
            """Defer the response and enqueue a list_cases event for Bot.dispatch."""
            await interaction.response.defer()
            channel_id = self._channel_id(interaction)
            self._pending[channel_id] = interaction
            await self._event_queue.put(
                PlatformEvent(
                    command="list_cases",
                    args={},
                    channel_id=channel_id,
                    user_id=str(interaction.user.id),
                    permission_level=self._permission_level(interaction),
                    raw=interaction,
                )
            )

    async def events(self) -> AsyncIterator[PlatformEvent]:  # type: ignore[override]
        """Yield events from the internal queue as Discord interactions arrive.

        Blocks until on_ready fires so no events are emitted before the client
        is fully connected and slash commands are synced.
        """
        await self._ready.wait()
        while True:
            yield await self._event_queue.get()

    async def send(self, channel_id: str, response: BotResponse) -> None:
        """Send a BotResponse as a Discord followup message.

        Pops the stored interaction for channel_id and calls followup.send().
        No-ops if the interaction is no longer available (e.g. already answered).
        """
        interaction = self._pending.pop(channel_id, None)
        if interaction is None:
            return
        content = _format_response(response)
        await interaction.followup.send(content=content, ephemeral=response.ephemeral)

    async def connect(self) -> None:
        """Fire the discord.py client as a background task.

        Returns immediately; the client runs concurrently with the bot's event loop.
        """
        asyncio.create_task(
            self._client.start(settings.discord_bot_token),
            name="discord-client",
        )

    async def disconnect(self) -> None:
        """Close the discord.py client gracefully."""
        await self._client.close()
