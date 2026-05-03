"""Per-command cooldown enforcement built on the `limits` library.

The dispatch loop calls `CooldownTracker.hit()` before invoking a command's
handler. A successful call records an attempt in the underlying rate limiter;
a denied call raises `CooldownError` with the exact remaining time.

Storage today is `MemoryStorage`. Swapping in `RedisStorage("redis://...")`
to share state across processes is a one-line change in `__init__`.
"""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Literal

from limits import RateLimitItemPerSecond
from limits.aio.storage import MemoryStorage
from limits.aio.strategies import MovingWindowRateLimiter

if TYPE_CHECKING:
    from docketmind.commands import CommandSpec
    from docketmind.platforms import PlatformEvent


CooldownScope = Literal["user", "channel", "guild", "global"]


class CooldownError(Exception):
    """Raised when a command is invoked before its cooldown expires."""

    def __init__(self, retry_after: float) -> None:
        """Initialise with the number of seconds remaining on the cooldown."""
        self.retry_after = retry_after
        super().__init__(f"Command on cooldown. Retry after {retry_after:.1f}s.")


def _scope_identifiers(
    spec: CommandSpec,
    event: PlatformEvent,
    platform_name: str,
) -> tuple[str, ...]:
    """Build the identifier tuple that defines a command's cooldown bucket.

    The first identifier is the command name so distinct commands don't share
    a bucket even when they have the same rate. The remaining identifiers
    depend on `spec.cooldown_scope`:

        global  -> (cmd, platform)
        user    -> (cmd, platform, user_id)
        guild   -> (cmd, platform, guild_id or "dm", user_id)   [default]
        channel -> (cmd, platform, channel_id, user_id)
    """
    cmd = spec.name
    scope: CooldownScope = spec.cooldown_scope
    if scope == "global":
        return (cmd, platform_name)
    if scope == "user":
        return (cmd, platform_name, event.user_id)
    if scope == "channel":
        return (cmd, platform_name, event.channel_id, event.user_id)
    # "guild" (default): DMs have no guild, so coalesce to a literal "dm" bucket.
    return (cmd, platform_name, event.guild_id or "dm", event.user_id)


def _rate_for(spec: CommandSpec) -> RateLimitItemPerSecond:
    """Map `spec.cooldown` (seconds, float) to a `limits` rate item.

    `RateLimitItemPerSecond(1, n)` means "1 request per n seconds". Sub-second
    cooldowns are rounded up to the nearest second; current callers all use
    whole-second values so this is lossless in practice.
    """
    seconds = max(1, math.ceil(spec.cooldown))
    return RateLimitItemPerSecond(1, seconds)


class CooldownTracker:
    """Enforces per-command, scope-aware cooldowns using a `limits` rate limiter.

    Construct one per process and inject it into `dispatch`. The default
    `MemoryStorage` keeps state in the current process; swap to a
    `RedisStorage` instance to share cooldowns across instances.

    Cooldowns are armed on attempt (the limiter increments before the handler
    runs), so a handler that raises still consumes the user's window. This
    matches the previous behaviour and prevents tight retry loops against a
    flaky backend.
    """

    def __init__(self, storage: MemoryStorage | None = None) -> None:
        """Initialise the underlying moving-window limiter and storage."""
        self._storage = storage or MemoryStorage()
        self._limiter = MovingWindowRateLimiter(self._storage)

    async def hit(
        self,
        spec: CommandSpec,
        event: PlatformEvent,
        platform_name: str,
    ) -> None:
        """Record an attempt; raise `CooldownError` if the user is still cooling down.

        No-ops for commands with `spec.cooldown <= 0`.
        """
        if spec.cooldown <= 0:
            return
        item = _rate_for(spec)
        identifiers = _scope_identifiers(spec, event, platform_name)
        if await self._limiter.hit(item, *identifiers):
            return
        stats = await self._limiter.get_window_stats(item, *identifiers)
        retry_after = max(0.0, stats.reset_time - time.time())
        raise CooldownError(retry_after=retry_after)

    async def reset(self) -> None:
        """Wipe all cooldown state in place. Primarily for test isolation.

        Mutates the underlying storage rather than replacing it, so callers
        that hold a direct reference to `tracker` (e.g. via
        `from docketmind.cooldown import tracker`) keep seeing the cleared
        state without any rebinding.
        """
        await self._storage.reset()


# Module-level singleton, matching the pattern used for `engine` and `index`.
tracker: CooldownTracker = CooldownTracker()
