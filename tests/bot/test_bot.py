"""Tests for dispatch routing, permission enforcement, and cooldown handling."""

from collections.abc import AsyncIterator

import pytest

from docketmind.__main__ import _cooldowns, _run_platform, dispatch
from docketmind.commands import CommandSpec
from docketmind.platforms import BotResponse, PermissionLevel, Platform, PlatformEvent


def _event(
    cmd: str = "ping",
    args: dict | None = None,
    user_id: str = "user-1",
    channel_id: str = "guild:channel",
    permission_level: PermissionLevel = PermissionLevel.USER,
) -> PlatformEvent:
    """Build a PlatformEvent with sensible test defaults."""
    return PlatformEvent(
        command=cmd,
        args=args or {},
        channel_id=channel_id,
        user_id=user_id,
        permission_level=permission_level,
    )


class DummyPlatform(Platform):
    """In-memory platform that yields events from a list and records sends."""

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
def _clear_cooldowns():
    """Reset the global cooldown caches between tests."""
    _cooldowns.clear()
    yield
    _cooldowns.clear()


# ------------------------------------------------------------------
# dispatch routing
# ------------------------------------------------------------------


async def test_dispatch_routes_to_registered_handler():
    """Dispatch should invoke the correct handler and send the response."""
    platform = DummyPlatform()
    called_with: list[PlatformEvent] = []

    async def handler(event: PlatformEvent) -> BotResponse:
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
        raise RuntimeError("boom")

    specs = {"fail": CommandSpec(name="fail", description="Fail", handler=bad_handler)}
    await dispatch(_event(cmd="fail"), platform, specs)

    assert len(platform.sent) == 1
    assert "internal error" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


# ------------------------------------------------------------------
# Permission enforcement (in dispatch)
# ------------------------------------------------------------------


async def test_permission_blocks_user_on_admin_command():
    """A USER-level caller should be rejected for ADMIN commands."""
    platform = DummyPlatform()

    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="secret")

    specs = {
        "admin": CommandSpec(
            name="admin",
            description="Admin",
            handler=admin_cmd,
            permission=PermissionLevel.ADMIN,
        )
    }
    await dispatch(_event(cmd="admin", permission_level=PermissionLevel.USER), platform, specs)

    assert len(platform.sent) == 1
    assert "permission" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


async def test_permission_allows_admin():
    """An ADMIN-level caller should pass the permission check."""
    platform = DummyPlatform()

    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="secret")

    specs = {
        "admin": CommandSpec(
            name="admin",
            description="Admin",
            handler=admin_cmd,
            permission=PermissionLevel.ADMIN,
        )
    }
    await dispatch(_event(cmd="admin", permission_level=PermissionLevel.ADMIN), platform, specs)

    assert len(platform.sent) == 1
    assert platform.sent[0][1].text == "secret"


# ------------------------------------------------------------------
# Cooldown enforcement (in dispatch)
# ------------------------------------------------------------------


async def test_cooldown_allows_first_call():
    """First invocation within cooldown window should succeed."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    specs = {"cd": CommandSpec(name="cd", description="t", handler=cmd, cooldown=60.0)}
    await dispatch(_event(cmd="cd", user_id="u1"), platform, specs)

    assert platform.sent[0][1].text == "ok"


async def test_cooldown_blocks_second_call_within_window():
    """Second call from the same user within the cooldown window should be rejected."""
    platform = DummyPlatform()

    async def cmd(event: PlatformEvent) -> BotResponse:
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
        return BotResponse(text="ok")

    specs = {"cd": CommandSpec(name="cd", description="t", handler=cmd, cooldown=60.0)}
    await dispatch(_event(cmd="cd", user_id="userA"), platform, specs)
    await dispatch(_event(cmd="cd", user_id="userB"), platform, specs)

    assert len(platform.sent) == 2
    assert all(r.text == "ok" for _, r in platform.sent)


# ------------------------------------------------------------------
# Event loop integration
# ------------------------------------------------------------------


async def test_run_platform_drives_event_loop():
    """_run_platform should process all events and dispatch each one."""
    events = [
        _event(cmd="ping", user_id="u1"),
        _event(cmd="ping", user_id="u2"),
        _event(cmd="ping", user_id="u3"),
    ]
    platform = DummyPlatform(events_to_emit=events)

    async def ping(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="pong")

    specs = {"ping": CommandSpec(name="ping", description="Ping", handler=ping)}
    await _run_platform(platform, specs)

    assert len(platform.sent) == 3
    assert all(r.text == "pong" for _, r in platform.sent)
