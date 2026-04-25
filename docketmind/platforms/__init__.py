"""Platform adapter package: contract types and abstract base for all adapters.

Import a specific platform adapter to register it with the Bot:
    from docketmind.platforms.discord import DiscordPlatform
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from docketmind.chat import SourceChunk

if TYPE_CHECKING:
    from docketmind.commands import CommandSpec


class PermissionLevel(IntEnum):
    """Abstract permission tiers shared across all platforms.

    Each platform adapter maps its native roles/flags to exactly one tier.
    Tiers are ordered so that >= comparisons work naturally.
    """

    USER = 0
    ADMIN = 1


class PlatformEvent(BaseModel):
    """A normalised command invocation from any platform.

    Produced by Platform.events() and consumed by Bot.dispatch().
    """

    command: str
    args: dict[str, Any]
    channel_id: str  # opaque string; platform-specific (e.g. "guild_id:channel_id")
    user_id: str
    permission_level: PermissionLevel
    raw: Any = Field(default=None, exclude=True)


class BotResponse(BaseModel):
    """A normalised reply that platforms render in their native format.

    text is always populated. citations are optional source chunks from RAG.
    ephemeral hints that only the requesting user should see the response.
    question echoes the original user query so adapters can render it as a header.
    """

    text: str
    citations: list[SourceChunk] = []
    ephemeral: bool = False
    question: str | None = None


class Platform(ABC):
    """Abstract interface every messaging platform adapter must implement.

    Platforms are passive: they emit events and accept responses.
    All control flow lives in Bot.
    """

    @abstractmethod
    def events(self) -> AsyncIterator[PlatformEvent]:
        """Yield normalised events as they arrive from the platform.

        This is a long-running async generator; the Bot iterates it forever.
        Implementations must handle reconnection internally.
        """
        ...

    @abstractmethod
    async def send(self, channel_id: str, response: BotResponse) -> None:
        """Deliver response to the given channel.

        channel_id is the same opaque string that arrived in PlatformEvent.channel_id.
        """
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Open the connection to the platform (login, websocket handshake, etc.)."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the platform connection."""
        ...

    def register_commands(self, specs: list[CommandSpec]) -> None:  # noqa: B027
        """Build platform-native command registrations from CommandSpec metadata.

        Override in adapters that need to translate specs into native UI
        elements (e.g. Discord slash commands). The default is a no-op.
        """
