"""Tests for dispatch routing, permission enforcement, and cooldown handling."""

import asyncio
import re
from collections.abc import AsyncIterator

import pytest

from docketmind import cooldown
from docketmind.__main__ import _run_platform, dispatch
from docketmind.commands import CommandSpec
from docketmind.platforms import BotResponse, PermissionLevel, Platform, PlatformEvent


def _event(
    cmd: str = "ping",
    args: dict | None = None,
    user_id: str = "user-1",
    channel_id: str = "guild-1:channel-1",
    guild_id: str | None = "guild-1",
    permission_level: PermissionLevel = PermissionLevel.USER,
) -> PlatformEvent:
    """Build a PlatformEvent with sensible test defaults."""
    return PlatformEvent(
        command=cmd,
        args=args or {},
        channel_id=channel_id,
        user_id=user_id,
        guild_id=guild_id,
        permission_level=permission_level,
    )


class DummyPlatform(Platform):
    """In-memory platform that yields events from a list and records sends."""

    name = "dummy"

    def __init__(self, events_to_emit: list[PlatformEvent] | None = None) -> None:
        """Initialise with an optional list of events to emit."""
        self._events: list[PlatformEvent] = events_to_emit or []
        self.sent: list[tuple[str, BotResponse]] = []
        self.connected = False
        self.disconnected = False

    async def events(self) -> AsyncIterator[PlatformEvent]:  # type: ignore[override]
        """Yield each pre-configured event once."""
        for e in self._events:
            yield e

    async def send(self, channel_id: str, response: BotResponse) -> None:
        """Record the send call."""
        self.sent.append((channel_id, response))

    async def connect(self) -> None:
        """Mark as connected."""
        self.connected = True

    async def disconnect(self) -> None:
        """Mark as disconnected."""
        self.disconnected = True


@pytest.fixture(autouse=True)
async def _reset_cooldown_state():
    """Wipe module-level cooldown state around each test for isolation."""
    await cooldown.reset()
    yield
    await cooldown.reset()


async def test_dispatch_routes_to_registered_handler():
    """Dispatch should invoke the correct handler and send the response."""
    platform = DummyPlatform()
    called_with: list[PlatformEvent] = []

    async def handler(event: PlatformEvent) -> BotResponse:
        """Capture the event and return a fixed pong response."""
        called_with.append(event)
        return BotResponse(text="pong")

    specs = {"ping": CommandSpec(name="ping", description="Ping", handler=handler)}
    evt = _event(cmd="ping")
    await dispatch(evt, platform, specs)

    assert len(called_with) == 1
    assert called_with[0] is evt
    assert len(platform.sent) == 1
    assert platform.sent[0][1].text == "pong"


async def test_dispatch_returns_ephemeral_error_for_unknown_command():
    """An unregistered command should produce an ephemeral error."""
    platform = DummyPlatform()
    await dispatch(_event(cmd="nope"), platform, {})

    assert len(platform.sent) == 1
    response = platform.sent[0][1]
    assert "Unknown command" in response.text
    assert response.ephemeral is True


async def test_dispatch_sends_error_on_handler_exception():
    """An unhandled handler exception should produce an ephemeral internal error."""
    platform = DummyPlatform()

    async def bad_handler(event: PlatformEvent) -> BotResponse:
        """Always raise so dispatch's exception path is exercised."""
        raise RuntimeError("boom")

    specs = {"fail": CommandSpec(name="fail", description="Fail", handler=bad_handler)}
    await dispatch(_event(cmd="fail"), platform, specs)

    assert len(platform.sent) == 1
    assert "internal error" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


async def test_permission_blocks_user_on_admin_command():
    """A USER-level caller should be rejected for ADMIN commands."""
    platform = DummyPlatform()

    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        """Admin-only handler that should never run for USER callers."""
        return BotResponse(text="secret")

    specs = {
        "admin": CommandSpec(
            name="admin",
            description="Admin",
            handler=admin_cmd,
            permission=PermissionLevel.ADMIN,
        )
    }
    await dispatch(
        _event(cmd="admin", permission_level=PermissionLevel.USER),
        platform,
        specs,
    )

    assert len(platform.sent) == 1
    assert "permission" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


async def test_permission_allows_admin():
    """An ADMIN-level caller should pass the permission check."""
    platform = DummyPlatform()

    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        """Admin-only handler returning the protected payload."""
        return BotResponse(text="secret")

    specs = {
        "admin": CommandSpec(
            name="admin",
            description="Admin",
            handler=admin_cmd,
            permission=PermissionLevel.ADMIN,
        )
    }
    await dispatch(
        _event(cmd="admin", permission_level=PermissionLevel.ADMIN),
        platform,
        specs,
    )

    assert len(platform.sent) == 1
    assert platform.sent[0][1].text == "secret"


async def test_cooldown_allows_first_call():
    """First invocation within cooldown window should succeed."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
        """Plain handler that always succeeds."""
        return BotResponse(text="ok")

    specs = {"cd": CommandSpec(name="cd", description="t", handler=cmd, cooldown=60.0)}
    await dispatch(_event(cmd="cd", user_id="u1"), platform, specs)

    assert platform.sent[0][1].text == "ok"


async def test_cooldown_blocks_second_call_within_window():
    """Second call from the same user within the cooldown window should be rejected."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
        """Plain handler that always succeeds."""
        return BotResponse(text="ok")

    specs = {"cd": CommandSpec(name="cd", description="t", handler=cmd, cooldown=60.0)}
    await dispatch(_event(cmd="cd", user_id="u2"), platform, specs)
    await dispatch(_event(cmd="cd", user_id="u2"), platform, specs)

    assert len(platform.sent) == 2
    assert platform.sent[0][1].text == "ok"
    assert "Slow down" in platform.sent[1][1].text
    assert platform.sent[1][1].ephemeral is True


async def test_cooldown_is_per_user_not_global():
    """Different users should not share a cooldown."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
        """Plain handler that always succeeds."""
        return BotResponse(text="ok")

    specs = {"cd": CommandSpec(name="cd", description="t", handler=cmd, cooldown=60.0)}
    await dispatch(_event(cmd="cd", user_id="userA"), platform, specs)
    await dispatch(_event(cmd="cd", user_id="userB"), platform, specs)

    assert len(platform.sent) == 2
    assert all(r.text == "ok" for _, r in platform.sent)


async def test_cooldown_default_scope_is_per_guild():
    """Same user, different guilds: default 'guild' scope should NOT share a bucket."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
        """Plain handler that always succeeds."""
        return BotResponse(text="ok")

    specs = {"cd": CommandSpec(name="cd", description="t", handler=cmd, cooldown=60.0)}
    await dispatch(
        _event(cmd="cd", user_id="u", guild_id="guild-A", channel_id="guild-A:c"),
        platform,
        specs,
    )
    await dispatch(
        _event(cmd="cd", user_id="u", guild_id="guild-B", channel_id="guild-B:c"),
        platform,
        specs,
    )

    assert len(platform.sent) == 2
    assert all(r.text == "ok" for _, r in platform.sent)


async def test_cooldown_default_scope_blocks_within_same_guild():
    """Same user in two channels of the same guild: default 'guild' scope blocks the second."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
        """Plain handler that always succeeds."""
        return BotResponse(text="ok")

    specs = {"cd": CommandSpec(name="cd", description="t", handler=cmd, cooldown=60.0)}
    await dispatch(
        _event(cmd="cd", user_id="u", guild_id="guild-A", channel_id="guild-A:c1"),
        platform,
        specs,
    )
    await dispatch(
        _event(cmd="cd", user_id="u", guild_id="guild-A", channel_id="guild-A:c2"),
        platform,
        specs,
    )

    assert len(platform.sent) == 2
    assert platform.sent[0][1].text == "ok"
    assert "Slow down" in platform.sent[1][1].text


async def test_cooldown_channel_scope_isolates_channels():
    """With cooldown_scope='channel', different channels must not share a bucket."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
        """Plain handler that always succeeds."""
        return BotResponse(text="ok")

    specs = {
        "cd": CommandSpec(
            name="cd",
            description="t",
            handler=cmd,
            cooldown=60.0,
            cooldown_scope="channel",
        )
    }
    await dispatch(
        _event(cmd="cd", user_id="u", guild_id="g", channel_id="g:c1"),
        platform,
        specs,
    )
    await dispatch(
        _event(cmd="cd", user_id="u", guild_id="g", channel_id="g:c2"),
        platform,
        specs,
    )

    assert len(platform.sent) == 2
    assert all(r.text == "ok" for _, r in platform.sent)


async def test_cooldown_global_scope_blocks_all_users():
    """With cooldown_scope='global', any second caller is rejected within the window."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
        """Plain handler that always succeeds."""
        return BotResponse(text="ok")

    specs = {
        "cd": CommandSpec(
            name="cd",
            description="t",
            handler=cmd,
            cooldown=60.0,
            cooldown_scope="global",
        )
    }
    await dispatch(_event(cmd="cd", user_id="userA"), platform, specs)
    await dispatch(_event(cmd="cd", user_id="userB"), platform, specs)

    assert len(platform.sent) == 2
    assert platform.sent[0][1].text == "ok"
    assert "Slow down" in platform.sent[1][1].text


async def test_cooldown_retry_after_is_accurate():
    """The reported retry_after should reflect time remaining, not the full window."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
        """Plain handler that always succeeds."""
        return BotResponse(text="ok")

    specs = {"cd": CommandSpec(name="cd", description="t", handler=cmd, cooldown=10.0)}
    await dispatch(_event(cmd="cd", user_id="u"), platform, specs)
    await asyncio.sleep(0.2)
    await dispatch(_event(cmd="cd", user_id="u"), platform, specs)

    msg = platform.sent[1][1].text
    match = re.search(r"in ([\d.]+)s", msg)
    assert match, f"expected a retry-in-Xs message, got: {msg!r}"
    retry_after = float(match.group(1))
    # Must be strictly less than the full window — guards against a regression
    # to the old behaviour of reporting `spec.cooldown` verbatim.
    assert retry_after < 10.0
    assert retry_after > 8.0


async def test_cooldown_arms_even_when_handler_raises():
    """Attempt-arm semantics: a crashed handler still counts against the user's window."""
    platform = DummyPlatform()
    call_count = 0

    async def flaky(event: PlatformEvent) -> BotResponse:
        """Always raise so we can confirm the cooldown still arms."""
        nonlocal call_count
        call_count += 1
        raise RuntimeError("boom")

    specs = {"cd": CommandSpec(name="cd", description="t", handler=flaky, cooldown=60.0)}
    await dispatch(_event(cmd="cd", user_id="u"), platform, specs)
    await dispatch(_event(cmd="cd", user_id="u"), platform, specs)

    assert len(platform.sent) == 2
    assert "internal error" in platform.sent[0][1].text.lower()
    assert "Slow down" in platform.sent[1][1].text
    # Second dispatch must short-circuit at the cooldown check.
    assert call_count == 1


async def test_run_platform_drives_event_loop():
    """_run_platform should process all events and dispatch each one."""
    events = [
        _event(cmd="ping", user_id="u1"),
        _event(cmd="ping", user_id="u2"),
        _event(cmd="ping", user_id="u3"),
    ]
    platform = DummyPlatform(events_to_emit=events)

    async def ping(event: PlatformEvent) -> BotResponse:
        """Return a fixed pong response for every ping."""
        return BotResponse(text="pong")

    specs = {"ping": CommandSpec(name="ping", description="Ping", handler=ping)}
    await _run_platform(platform, specs)

    assert len(platform.sent) == 3
    assert all(r.text == "pong" for _, r in platform.sent)
