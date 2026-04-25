"""Tests for Bot orchestrator and dispatch behaviour."""

from collections.abc import AsyncIterator

import pytest

from docketmind.bot import Bot
from docketmind.commands import (
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


# ------------------------------------------------------------------
# Bot.dispatch routing
# ------------------------------------------------------------------


async def test_dispatch_routes_to_registered_handler():
    bot = Bot()
    platform = DummyPlatform()
    called_with: list[PlatformEvent] = []

    async def handler(event: PlatformEvent) -> BotResponse:
        called_with.append(event)
        return BotResponse(text="pong")

    bot.register_commands([CommandSpec(name="ping", description="Ping", handler=handler)])
    evt = _event(cmd="ping")
    await bot.dispatch(evt, platform)

    assert len(called_with) == 1
    assert called_with[0] is evt
    assert len(platform.sent) == 1
    assert platform.sent[0][1].text == "pong"


async def test_dispatch_returns_ephemeral_error_for_unknown_command():
    bot = Bot()
    platform = DummyPlatform()

    await bot.dispatch(_event(cmd="nope"), platform)

    assert len(platform.sent) == 1
    response = platform.sent[0][1]
    assert "Unknown command" in response.text
    assert response.ephemeral is True


async def test_dispatch_sends_error_on_handler_exception():
    bot = Bot()
    platform = DummyPlatform()

    async def bad_handler(event: PlatformEvent) -> BotResponse:
        raise RuntimeError("boom")

    bot.register_commands([CommandSpec(name="fail", description="Fail", handler=bad_handler)])
    await bot.dispatch(_event(cmd="fail"), platform)

    assert len(platform.sent) == 1
    assert "internal error" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


# ------------------------------------------------------------------
# @command decorator: cooldown enforcement
# ------------------------------------------------------------------


async def test_cooldown_allows_first_call():
    @command(name="_test_cd1", description="t", cooldown=60.0)
    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    response = await cmd(_event(user_id="u1"))
    assert response.text == "ok"


async def test_cooldown_blocks_second_call_within_window():
    @command(name="_test_cd2", description="t", cooldown=60.0)
    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    await cmd(_event(user_id="u2"))

    with pytest.raises(CooldownError) as exc_info:
        await cmd(_event(user_id="u2"))

    assert exc_info.value.retry_after > 0


async def test_cooldown_is_per_user_not_global():
    """Different users should not share a cooldown."""

    @command(name="_test_cd3", description="t", cooldown=60.0)
    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    await cmd(_event(user_id="userA"))
    response = await cmd(_event(user_id="userB"))
    assert response.text == "ok"


async def test_cooldown_wrapped_in_dispatch_returns_error_response():
    bot = Bot()
    platform = DummyPlatform()

    @command(name="_test_cd4", description="t", cooldown=60.0)
    async def cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    bot.register_commands([cmd.__command_spec__])  # type: ignore[attr-defined]

    await bot.dispatch(_event(cmd="_test_cd4", user_id="u3"), platform)
    await bot.dispatch(_event(cmd="_test_cd4", user_id="u3"), platform)

    assert len(platform.sent) == 2
    first, second = platform.sent
    assert first[1].text == "ok"
    assert "Slow down" in second[1].text
    assert second[1].ephemeral is True


# ------------------------------------------------------------------
# @command decorator: permission enforcement
# ------------------------------------------------------------------


async def test_permission_blocks_user_on_admin_command():
    @command(name="_test_perm1", description="t", permission=PermissionLevel.ADMIN)
    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="secret")

    with pytest.raises(PermissionDeniedError):
        await admin_cmd(_event(permission_level=PermissionLevel.USER))


async def test_permission_allows_admin():
    @command(name="_test_perm2", description="t", permission=PermissionLevel.ADMIN)
    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="secret")

    response = await admin_cmd(_event(permission_level=PermissionLevel.ADMIN))
    assert response.text == "secret"


async def test_permission_wrapped_in_dispatch_returns_error_response():
    bot = Bot()
    platform = DummyPlatform()

    @command(name="_test_perm3", description="t", permission=PermissionLevel.ADMIN)
    async def admin_cmd(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="secret")

    bot.register_commands([admin_cmd.__command_spec__])  # type: ignore[attr-defined]
    await bot.dispatch(_event(cmd="_test_perm3", permission_level=PermissionLevel.USER), platform)

    assert len(platform.sent) == 1
    assert "permission" in platform.sent[0][1].text.lower()
    assert platform.sent[0][1].ephemeral is True


# ------------------------------------------------------------------
# Bot platform + command registration
# ------------------------------------------------------------------


def test_register_platform_stores_instance():
    bot = Bot()
    p = DummyPlatform()
    bot.register_platform(p)
    assert bot._platforms[0] is p


def test_register_commands_bulk_registers_handlers():
    bot = Bot()

    async def handler_a(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="a")

    async def handler_b(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="b")

    specs = [
        CommandSpec(name="alpha", description="Alpha cmd", handler=handler_a),
        CommandSpec(name="beta", description="Beta cmd", handler=handler_b),
    ]
    bot.register_commands(specs)

    assert bot._handlers["alpha"] is handler_a
    assert bot._handlers["beta"] is handler_b
    assert len(bot._handlers) == 2


async def test_register_commands_handlers_are_dispatchable():
    bot = Bot()
    platform = DummyPlatform()

    async def pong(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="pong")

    bot.register_commands([CommandSpec(name="ping", description="Ping", handler=pong)])
    await bot.dispatch(_event(cmd="ping"), platform)

    assert len(platform.sent) == 1
    assert platform.sent[0][1].text == "pong"


async def test_run_drives_event_loop():
    """Bot.run() should process all events from a DummyPlatform."""
    bot = Bot()
    events = [
        _event(cmd="ping", user_id="u1"),
        _event(cmd="ping", user_id="u2"),
        _event(cmd="ping", user_id="u3"),
    ]
    platform = DummyPlatform(events_to_emit=events)
    bot.register_platform(platform)

    async def ping(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="pong")

    bot.register_commands([CommandSpec(name="ping", description="Ping", handler=ping)])
    await bot.run()

    assert platform.connected is True
    assert platform.disconnected is True
    assert len(platform.sent) == 3
    assert all(r.text == "pong" for _, r in platform.sent)
