"""Tests for dispatch routing and error handling."""

from collections.abc import AsyncIterator

import pytest

from docketmind.__main__ import _run_platform, dispatch
from docketmind.commands import (
    CommandHandler,
    CommandSpec,
    CooldownError,
    PermissionDeniedError,
    command,
)
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


def _handlers_from_specs(specs: list[CommandSpec]) -> dict[str, CommandHandler]:
    """Build a name -> handler dict from CommandSpec objects."""
    return {s.name: s.handler for s in specs}


# ------------------------------------------------------------------
# dispatch routing
# ------------------------------------------------------------------


async def test_dispatch_routes_to_registered_handler():
    """Dispatch should invoke the correct handler and send the response."""
    platform = DummyPlatform()
    called_with: list[PlatformEvent] = []

    async def handler(event: PlatformEvent) -> BotResponse:
        """Echo handler."""
        called_with.append(event)
        return BotResponse(text="pong")

    handlers = _handlers_from_specs([CommandSpec(name="ping", description="Ping", handler=handler)])
    evt = _event(cmd="ping")
    await dispatch(evt, platform, handlers)

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
        """Always raises."""
        raise RuntimeError("boom")

    handlers = _handlers_from_specs(
        [CommandSpec(name="fail", description="Fail", handler=bad_handler)]
    )
    await dispatch(_event(cmd="fail"), platform, handlers)

    assert len(platform.sent) == 1
    assert "internal error" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


# ------------------------------------------------------------------
# @command decorator: cooldown enforcement
# ------------------------------------------------------------------


async def test_cooldown_allows_first_call():
    """First invocation within cooldown window should succeed."""

    @command(name="_test_cd1", description="t", cooldown=60.0)
    async def cmd(event: PlatformEvent) -> BotResponse:
        """Cooldown test handler."""
        return BotResponse(text="ok")

    response = await cmd(_event(user_id="u1"))
    assert response.text == "ok"


async def test_cooldown_blocks_second_call_within_window():
    """Second call from the same user within the cooldown window should raise."""

    @command(name="_test_cd2", description="t", cooldown=60.0)
    async def cmd(event: PlatformEvent) -> BotResponse:
        """Cooldown test handler."""
        return BotResponse(text="ok")

    await cmd(_event(user_id="u2"))

    with pytest.raises(CooldownError) as exc_info:
        await cmd(_event(user_id="u2"))

    assert exc_info.value.retry_after > 0


async def test_cooldown_is_per_user_not_global():
    """Different users should not share a cooldown."""

    @command(name="_test_cd3", description="t", cooldown=60.0)
    async def cmd(event: PlatformEvent) -> BotResponse:
        """Cooldown test handler."""
        return BotResponse(text="ok")

    await cmd(_event(user_id="userA"))
    response = await cmd(_event(user_id="userB"))
    assert response.text == "ok"


async def test_cooldown_wrapped_in_dispatch_returns_error_response():
    """CooldownError from a handler should be turned into an ephemeral message."""
    platform = DummyPlatform()

    @command(name="_test_cd4", description="t", cooldown=60.0)
    async def cmd(event: PlatformEvent) -> BotResponse:
        """Cooldown test handler."""
        return BotResponse(text="ok")

    handlers = _handlers_from_specs(
        [cmd.__command_spec__]  # type: ignore[attr-defined]
    )

    await dispatch(_event(cmd="_test_cd4", user_id="u3"), platform, handlers)
    await dispatch(_event(cmd="_test_cd4", user_id="u3"), platform, handlers)

    assert len(platform.sent) == 2
    first, second = platform.sent
    assert first[1].text == "ok"
    assert "Slow down" in second[1].text
    assert second[1].ephemeral is True


# ------------------------------------------------------------------
# @command decorator: permission enforcement
# ------------------------------------------------------------------


async def test_permission_blocks_user_on_admin_command():
    """A USER-level caller should be rejected for ADMIN commands."""

    @command(name="_test_perm1", description="t", permission=PermissionLevel.ADMIN)
    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        """Admin-only handler."""
        return BotResponse(text="secret")

    with pytest.raises(PermissionDeniedError):
        await admin_cmd(_event(permission_level=PermissionLevel.USER))


async def test_permission_allows_admin():
    """An ADMIN-level caller should pass the permission check."""

    @command(name="_test_perm2", description="t", permission=PermissionLevel.ADMIN)
    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        """Admin-only handler."""
        return BotResponse(text="secret")

    response = await admin_cmd(_event(permission_level=PermissionLevel.ADMIN))
    assert response.text == "secret"


async def test_permission_wrapped_in_dispatch_returns_error_response():
    """PermissionDeniedError should be turned into an ephemeral message."""
    platform = DummyPlatform()

    @command(name="_test_perm3", description="t", permission=PermissionLevel.ADMIN)
    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        """Admin-only handler."""
        return BotResponse(text="secret")

    handlers = _handlers_from_specs(
        [admin_cmd.__command_spec__]  # type: ignore[attr-defined]
    )
    await dispatch(
        _event(cmd="_test_perm3", permission_level=PermissionLevel.USER),
        platform,
        handlers,
    )

    assert len(platform.sent) == 1
    assert "permission" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


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
        """Ping handler."""
        return BotResponse(text="pong")

    handlers = _handlers_from_specs([CommandSpec(name="ping", description="Ping", handler=ping)])
    await _run_platform(platform, handlers)

    assert len(platform.sent) == 3
    assert all(r.text == "pong" for _, r in platform.sent)
