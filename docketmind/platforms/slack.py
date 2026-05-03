"""Slack platform adapter using Slack Bolt (async socket mode)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from loguru import logger
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp

from docketmind.chat import SourceChunk
from docketmind.commands import CommandSpec
from docketmind.configure import settings
from docketmind.platforms import (
    BotResponse,
    PermissionLevel,
    Platform,
    PlatformEvent,
    PlatformNotConfigured,
)

_SLACK_TEXT_LIMIT = 3000


def _readable_date(iso: str | None) -> str:
    """Convert an ISO date string to a short human-readable form."""
    if not iso:
        return ""
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(iso)
        return dt.strftime("%b %-d, %Y")
    except (ValueError, TypeError):
        return iso


def _truncate(text: str, limit: int) -> str:
    """Truncate text at a word boundary, preserving newlines."""
    if len(text) <= limit:
        return text
    truncated = text[: limit - 3]
    last_space = truncated.rfind(" ")
    if last_space > limit // 2:
        truncated = truncated[:last_space]
    return truncated + "..."


class SlackPlatform(Platform):
    """Maps Slack slash commands to PlatformEvent/BotResponse via socket mode.

    Uses Slack Bolt's AsyncApp with socket mode so no public HTTP endpoint is
    needed. Commands are registered as Slack slash commands; responses use
    Block Kit for rich formatting.

    Permission mapping:
        Workspace admins/owners -> PermissionLevel.ADMIN
        All others              -> PermissionLevel.USER

    channel_id encoding:
        f"{team_id}:{channel_id}"
    """

    name = "slack"

    def __init__(self) -> None:
        """Initialise the Slack Bolt app and socket mode handler."""
        if not settings.slack_bot_token or not settings.slack_app_token:
            raise PlatformNotConfigured("slack: SLACK_BOT_TOKEN and SLACK_APP_TOKEN not set")

        self._app = AsyncApp(token=settings.slack_bot_token)
        self._handler = AsyncSocketModeHandler(self._app, settings.slack_app_token)
        self._event_queue: asyncio.Queue[PlatformEvent] = asyncio.Queue()
        self._pending: dict[str, list[Any]] = {}

    def register_commands(self, specs: list[CommandSpec]) -> None:
        """Register a Slack slash command listener for each CommandSpec."""
        for spec in specs:
            self._add_slash_command(spec)

    def _add_slash_command(self, spec: CommandSpec) -> None:
        """Wire a single CommandSpec as a Slack slash command handler."""

        async def handler(ack: Any, command: dict[str, Any]) -> None:
            await ack()
            channel_id = self._channel_id(command)
            self._pending.setdefault(channel_id, []).append(command)

            args = self._parse_args(spec, command.get("text", ""))

            await self._event_queue.put(
                PlatformEvent(
                    command=spec.name,
                    args=args,
                    channel_id=channel_id,
                    user_id=command["user_id"],
                    guild_id=command.get("team_id"),
                    permission_level=self._permission_level(command),
                    raw=command,
                )
            )

        self._app.command(f"/{spec.name}")(handler)

    @staticmethod
    def _channel_id(command: dict[str, Any]) -> str:
        """Build an opaque channel_id from team and channel."""
        return f"{command.get('team_id', 'T')}:{command.get('channel_id', 'C')}"

    @staticmethod
    def _permission_level(command: dict[str, Any]) -> PermissionLevel:
        """Map Slack command context to a PermissionLevel.

        Checks if the user is a workspace admin/owner via the command payload.
        Falls back to USER if not determinable from the slash command payload.
        """
        # Slack doesn't include admin status in slash command payloads by default.
        # For now, we treat all users as USER. Admin checks can be extended via
        # the users.info API call or a configured admin user list.
        return PermissionLevel.USER

    @staticmethod
    def _parse_args(spec: CommandSpec, text: str) -> dict[str, Any]:
        """Parse the slash command text into args matching the spec's params.

        Slack sends all args as a single text string. We split by whitespace
        and assign positionally to required params, then optional ones.
        """
        parts = text.split() if text.strip() else []
        args: dict[str, Any] = {}
        for i, param in enumerate(spec.params):
            if i < len(parts):
                args[param.name] = parts[i]
            elif not param.required:
                args[param.name] = None
            else:
                args[param.name] = ""
        return args

    # ------------------------------------------------------------------
    # Platform interface
    # ------------------------------------------------------------------

    async def events(self) -> AsyncIterator[PlatformEvent]:  # type: ignore[override]
        """Yield events from the internal queue as Slack commands arrive."""
        while True:
            yield await self._event_queue.get()

    async def send(self, channel_id: str, response: BotResponse) -> None:
        """Send a BotResponse back to Slack as a message in the channel.

        Uses Block Kit for RAG answers with sources, plain text otherwise.
        """
        pending = self._pending.get(channel_id)
        if not pending:
            return
        command = pending.pop(0)
        if not pending:
            del self._pending[channel_id]

        channel = command.get("channel_id", "")
        response_url = command.get("response_url")

        if response.question or response.citations:
            blocks = self._build_blocks(response)
            text_fallback = _truncate(response.text, _SLACK_TEXT_LIMIT)
            if response_url:
                from slack_sdk.webhook.async_client import AsyncWebhookClient

                webhook = AsyncWebhookClient(response_url)
                await webhook.send(
                    text=text_fallback,
                    blocks=blocks,
                    response_type="ephemeral" if response.ephemeral else "in_channel",
                )
            else:
                await self._app.client.chat_postMessage(
                    channel=channel,
                    text=text_fallback,
                    blocks=blocks,
                )
        else:
            text = _truncate(response.text, _SLACK_TEXT_LIMIT)
            if response_url:
                from slack_sdk.webhook.async_client import AsyncWebhookClient

                webhook = AsyncWebhookClient(response_url)
                await webhook.send(
                    text=text,
                    response_type="ephemeral" if response.ephemeral else "in_channel",
                )
            else:
                await self._app.client.chat_postMessage(channel=channel, text=text)

    # ------------------------------------------------------------------
    # Response rendering (Block Kit)
    # ------------------------------------------------------------------

    @staticmethod
    def _source_label(src: SourceChunk) -> str:
        """Build a Slack mrkdwn label for a source citation."""
        if src.pdf_url:
            filename = src.pdf_url.rstrip("/").split("/")[-1]
            return f"<{src.pdf_url}|{filename}>"

        name = src.title or "Docket entry"
        if src.court_listener_id and src.court_listener_id.startswith("http"):
            return f"<{src.court_listener_id}|{name}>"
        return name

    @staticmethod
    def _build_blocks(response: BotResponse) -> list[dict[str, Any]]:
        """Build Slack Block Kit blocks for a RAG answer with sources."""
        blocks: list[dict[str, Any]] = []

        if response.question:
            blocks.append(
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": _truncate(response.question, 150),
                    },
                }
            )

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": _truncate(response.text, _SLACK_TEXT_LIMIT),
                },
            }
        )

        if response.citations:
            blocks.append({"type": "divider"})
            source_lines: list[str] = []
            for i, src in enumerate(response.citations[:5], start=1):
                date = _readable_date(src.date_filed)
                label = SlackPlatform._source_label(src)
                entry = f"{i}. {label}"
                if date:
                    entry += f" — {date}"
                source_lines.append(entry)

            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "*Sources:*\n" + "\n".join(source_lines),
                        }
                    ],
                }
            )

        return blocks

    async def connect(self) -> None:
        """Start the Slack socket mode handler as a background task."""
        asyncio.create_task(
            self._handler.start_async(),
            name="slack-socket-mode",
        )
        logger.info("Slack adapter connected (socket mode)")

    async def disconnect(self) -> None:
        """Close the Slack socket mode connection."""
        await self._handler.close_async()
        logger.info("Slack adapter disconnected")
