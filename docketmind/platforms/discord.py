"""Discord platform adapter using discord.py 2.x slash commands."""

import asyncio
import inspect
import textwrap
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands as ext_commands
from loguru import logger

from docketmind.commands import CommandSpec
from docketmind.configure import settings
from docketmind.platforms import BotResponse, PermissionLevel, Platform, PlatformEvent

# Maximum characters in a Discord message
_DISCORD_MAX_LENGTH = 2000
# Reserve room for citations; truncate answer text at this limit
_ANSWER_MAX_LENGTH = 1800


def _format_response(response: BotResponse) -> str:
    """Render a BotResponse as a Discord message string.

    Truncates the answer text at word boundaries to _ANSWER_MAX_LENGTH and
    appends up to 5 source citations. The combined output never exceeds
    _DISCORD_MAX_LENGTH.
    """
    text = textwrap.shorten(response.text, width=_ANSWER_MAX_LENGTH, placeholder="...")
    if response.citations:
        citation_lines = [
            f"[{i}] {src.date_filed or 'unknown date'} — {src.pdf_url}"
            if src.pdf_url
            else f"[{i}] {src.date_filed or 'unknown date'}"
            for i, src in enumerate(response.citations[:5], start=1)
        ]
        text += "\n\n**Sources:**\n" + "\n".join(citation_lines)
    return textwrap.shorten(text, width=_DISCORD_MAX_LENGTH, placeholder="...")


class DiscordPlatform(Platform):
    """Maps discord.py slash command interactions to PlatformEvent/BotResponse.

    Slash commands are built automatically from CommandSpec metadata via
    register_commands(). Each spec becomes a single slash command whose
    parameters, descriptions, and defer behaviour are derived from the spec.

    Permission mapping:
        interaction.user.guild_permissions.administrator -> PermissionLevel.ADMIN
        all others                                        -> PermissionLevel.USER

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
        self._pending: dict[str, deque[discord.Interaction]] = {}
        self._ready = asyncio.Event()
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

    # ------------------------------------------------------------------
    # CommandSpec -> slash command auto-wiring
    # ------------------------------------------------------------------

    def register_commands(self, specs: list[CommandSpec]) -> None:
        """Build a Discord slash command for each CommandSpec and add it to the tree."""
        for spec in specs:
            self._add_slash_command(spec)

    def _add_slash_command(self, spec: CommandSpec) -> None:
        """Translate a single CommandSpec into an app_commands.Command on the tree."""

        async def callback(interaction: discord.Interaction, **kwargs: Any) -> None:
            await interaction.response.defer(ephemeral=spec.ephemeral_defer)
            ch = self._channel_id(interaction)
            self._pending.setdefault(ch, deque()).append(interaction)
            await self._event_queue.put(
                PlatformEvent(
                    command=spec.name,
                    args=kwargs,
                    channel_id=ch,
                    user_id=str(interaction.user.id),
                    permission_level=self._permission_level(interaction),
                    raw=interaction,
                )
            )

        sig_params = [
            inspect.Parameter(
                "interaction",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=discord.Interaction,
            ),
        ]
        descriptions: dict[str, str] = {}
        for p in spec.params:
            annotation = p.type if p.required else (p.type | None)
            default = inspect.Parameter.empty if p.required else None
            sig_params.append(
                inspect.Parameter(
                    p.name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=annotation,
                    default=default,
                )
            )
            descriptions[p.name] = p.description

        callback.__signature__ = inspect.Signature(sig_params)  # type: ignore[attr-defined]
        if descriptions:
            callback = app_commands.describe(**descriptions)(callback)

        self._tree.add_command(
            app_commands.Command(
                name=spec.name,
                description=spec.description,
                callback=callback,
            )
        )

    # ------------------------------------------------------------------
    # Platform interface
    # ------------------------------------------------------------------

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

        Pops the oldest stored interaction for channel_id (FIFO) and calls
        followup.send(). No-ops if no interaction is available.
        """
        q = self._pending.get(channel_id)
        if not q:
            return
        interaction = q.popleft()
        if not q:
            del self._pending[channel_id]
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
