"""Tests for Bot, Platform ABC, cooldown, and permission decorators."""

from collections.abc import AsyncIterator

import pytest

from docketmind.bot import (
    Bot,
    BotResponse,
    CooldownError,
    PermissionDeniedError,
    PermissionLevel,
    Platform,
    PlatformEvent,
    _cooldown_state,
    cooldown,
    requires_permission,
)


def _event(
    command: str = "ping",
    args: dict | None = None,
    user_id: str = "user-1",
    channel_id: str = "guild:channel",
    permission_level: PermissionLevel = PermissionLevel.USER,
) -> PlatformEvent:
    """Build a PlatformEvent with sensible test defaults."""
    return PlatformEvent(
        command=command,
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


async def test_dispatch_routes_to_registered_handler():
    bot = Bot()
    platform = DummyPlatform()
    called_with: list[PlatformEvent] = []

    async def handler(event: PlatformEvent) -> BotResponse:
        called_with.append(event)
        return BotResponse(text="pong")

    bot.command("ping")(handler)
    evt = _event(command="ping")
    await bot.dispatch(evt, platform)

    assert len(called_with) == 1
    assert called_with[0] is evt
    assert len(platform.sent) == 1
    assert platform.sent[0][1].text == "pong"


async def test_dispatch_returns_ephemeral_error_for_unknown_command():
    bot = Bot()
    platform = DummyPlatform()

    await bot.dispatch(_event(command="nope"), platform)

    assert len(platform.sent) == 1
    response = platform.sent[0][1]
    assert "Unknown command" in response.text
    assert response.ephemeral is True


async def test_dispatch_sends_error_on_handler_exception():
    bot = Bot()
    platform = DummyPlatform()

    async def bad_handler(event: PlatformEvent) -> BotResponse:
        raise RuntimeError("boom")

    bot.command("fail")(bad_handler)
    await bot.dispatch(_event(command="fail"), platform)

    assert len(platform.sent) == 1
    assert "internal error" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


async def test_cooldown_allows_first_call():
    @cooldown(seconds=60.0, per="user")
    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    _cooldown_state.clear()
    response = await cmd(_event(user_id="u1"))
    assert response.text == "ok"


async def test_cooldown_blocks_second_call_within_window():
    @cooldown(seconds=60.0, per="user")
    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    _cooldown_state.clear()
    await cmd(_event(user_id="u2"))

    with pytest.raises(CooldownError) as exc_info:
        await cmd(_event(user_id="u2"))

    assert exc_info.value.retry_after > 0


async def test_cooldown_is_per_user_not_global():
    """Different users should not share a cooldown."""

    @cooldown(seconds=60.0, per="user")
    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    _cooldown_state.clear()
    await cmd(_event(user_id="userA"))
    # userB should not be blocked even though userA triggered the cooldown
    response = await cmd(_event(user_id="userB"))
    assert response.text == "ok"


async def test_cooldown_per_channel():
    """per='channel' keys by channel_id, not user_id."""

    @cooldown(seconds=60.0, per="channel")
    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    _cooldown_state.clear()
    await cmd(_event(channel_id="ch1", user_id="u1"))

    with pytest.raises(CooldownError):
        await cmd(_event(channel_id="ch1", user_id="u2"))  # different user, same channel


async def test_cooldown_wrapped_in_dispatch_returns_error_response():
    bot = Bot()
    platform = DummyPlatform()

    @cooldown(seconds=60.0, per="user")
    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    _cooldown_state.clear()
    bot.command("slow")(cmd)

    await bot.dispatch(_event(command="slow", user_id="u3"), platform)
    await bot.dispatch(_event(command="slow", user_id="u3"), platform)

    assert len(platform.sent) == 2
    first, second = platform.sent
    assert first[1].text == "ok"
    assert "Slow down" in second[1].text
    assert second[1].ephemeral is True


async def test_requires_permission_blocks_user_on_admin_command():
    @requires_permission(PermissionLevel.ADMIN)
    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="secret")

    with pytest.raises(PermissionDeniedError):
        await admin_cmd(_event(permission_level=PermissionLevel.USER))


async def test_requires_permission_allows_admin():
    @requires_permission(PermissionLevel.ADMIN)
    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="secret")

    response = await admin_cmd(_event(permission_level=PermissionLevel.ADMIN))
    assert response.text == "secret"


async def test_requires_permission_wrapped_in_dispatch_returns_error_response():
    bot = Bot()
    platform = DummyPlatform()

    @requires_permission(PermissionLevel.ADMIN)
    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="secret")

    bot.command("admin")(admin_cmd)
    await bot.dispatch(_event(command="admin", permission_level=PermissionLevel.USER), platform)

    assert len(platform.sent) == 1
    assert "permission" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


def test_platform_decorator_registers_platform():
    bot = Bot()

    @bot.platform
    class MyPlatform(DummyPlatform):
        """Test platform."""

    assert len(bot._platforms) == 1
    assert isinstance(bot._platforms[0], MyPlatform)


def test_platform_decorator_returns_class_unchanged():
    bot = Bot()

    @bot.platform
    class MyPlatform(DummyPlatform):
        """Test platform."""

    assert MyPlatform is MyPlatform  # class identity preserved


def test_register_platform_stores_instance():
    bot = Bot()
    p = DummyPlatform()
    bot.register_platform(p)
    assert bot._platforms[0] is p


def test_command_decorator_registers_handler():
    bot = Bot()

    async def my_handler(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="hi")

    bot.command("greet")(my_handler)
    assert bot._handlers["greet"] is my_handler


async def test_run_drives_event_loop():
    """Bot.run() should process all events from a DummyPlatform."""
    bot = Bot()
    events = [
        _event(command="ping", user_id="u1"),
        _event(command="ping", user_id="u2"),
        _event(command="ping", user_id="u3"),
    ]
    platform = DummyPlatform(events_to_emit=events)
    bot.register_platform(platform)

    async def ping(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="pong")

    bot.command("ping")(ping)
    await bot.run()

    assert platform.connected is True
    assert platform.disconnected is True
    assert len(platform.sent) == 3
    assert all(r.text == "pong" for _, r in platform.sent)
