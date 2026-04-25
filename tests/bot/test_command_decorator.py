"""Tests for the Command ABC and @command decorator."""

import pytest

from docketmind.commands import Command, _last_called, _registry, command
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent


@pytest.fixture(autouse=True)
def clean_state():
    """Clear registry and cooldown state between tests."""
    _registry.clear()
    _last_called.clear()
    yield
    _registry.clear()
    _last_called.clear()


def _event(
    user_id: str = "u1",
    permission_level: PermissionLevel = PermissionLevel.USER,
) -> PlatformEvent:
    """Build a PlatformEvent with fixed command/channel for decorator tests."""
    return PlatformEvent(
        command="test_cmd",
        args={},
        channel_id="ch1",
        user_id=user_id,
        permission_level=permission_level,
    )


def test_command_stamps_metadata_onto_class():
    @command(
        name="greet",
        description="Say hi",
        cooldown=0.0,
        permission=PermissionLevel.USER,
    )
    class GreetCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="hi")

    assert GreetCommand.name == "greet"
    assert GreetCommand.description == "Say hi"
    assert GreetCommand.cooldown == 0.0
    assert GreetCommand.permission == PermissionLevel.USER


def test_command_registers_instance_in_registry():
    @command(
        name="greet",
        description="Say hi",
        cooldown=0.0,
        permission=PermissionLevel.USER,
    )
    class GreetCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="hi")

    assert "greet" in _registry
    assert isinstance(_registry["greet"], GreetCommand)


def test_command_returns_the_class_unchanged():
    @command(
        name="greet",
        description="Say hi",
        cooldown=0.0,
        permission=PermissionLevel.USER,
    )
    class GreetCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="hi")

    # The decorator must return the class, not an instance
    assert isinstance(GreetCommand, type)


async def test_call_blocks_user_on_admin_command():
    @command(
        name="admin_cmd",
        description="Admin only",
        cooldown=0.0,
        permission=PermissionLevel.ADMIN,
    )
    class AdminCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="secret")

    cmd = _registry["admin_cmd"]
    response = await cmd(_event(permission_level=PermissionLevel.USER))

    assert response.ephemeral is True
    assert "permission" in response.text.lower()


async def test_call_allows_admin_on_admin_command():
    @command(
        name="admin_cmd",
        description="Admin only",
        cooldown=0.0,
        permission=PermissionLevel.ADMIN,
    )
    class AdminCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="secret")

    cmd = _registry["admin_cmd"]
    response = await cmd(_event(permission_level=PermissionLevel.ADMIN))

    assert response.text == "secret"


async def test_call_allows_first_invocation():
    @command(
        name="slow_cmd",
        description="Has cooldown",
        cooldown=60.0,
        permission=PermissionLevel.USER,
    )
    class SlowCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="ok")

    cmd = _registry["slow_cmd"]
    response = await cmd(_event(user_id="u1"))
    assert response.text == "ok"


async def test_call_blocks_second_invocation_within_cooldown():
    @command(
        name="slow_cmd",
        description="Has cooldown",
        cooldown=60.0,
        permission=PermissionLevel.USER,
    )
    class SlowCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="ok")

    cmd = _registry["slow_cmd"]
    await cmd(_event(user_id="u2"))
    response = await cmd(_event(user_id="u2"))

    assert response.ephemeral is True
    assert "slow down" in response.text.lower()


async def test_cooldown_is_per_user():
    @command(
        name="slow_cmd",
        description="Has cooldown",
        cooldown=60.0,
        permission=PermissionLevel.USER,
    )
    class SlowCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="ok")

    cmd = _registry["slow_cmd"]
    await cmd(_event(user_id="userA"))
    # userB is a different user — must not be blocked
    response = await cmd(_event(user_id="userB"))
    assert response.text == "ok"


async def test_execute_bypasses_enforcement():
    """Calling execute() directly skips permission and cooldown checks."""

    @command(
        name="admin_cmd",
        description="Admin only",
        cooldown=60.0,
        permission=PermissionLevel.ADMIN,
    )
    class AdminCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="secret")

    cmd = AdminCommand()  # fresh instance, not from registry
    response = await cmd.execute(_event(permission_level=PermissionLevel.USER))
    assert response.text == "secret"


def test_load_registers_all_commands_with_bot():
    from unittest.mock import MagicMock

    from docketmind.commands import load

    @command(name="alpha", description="Alpha", cooldown=0.0, permission=PermissionLevel.USER)
    class AlphaCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="alpha")

    @command(name="beta", description="Beta", cooldown=0.0, permission=PermissionLevel.USER)
    class BetaCommand(Command):
        async def execute(self, event: PlatformEvent) -> BotResponse:
            return BotResponse(text="beta")

    bot = MagicMock()
    bot.command.return_value = lambda fn: fn
    load(bot)

    assert bot.command.call_count == 2
    called_names = {call.args[0] for call in bot.command.call_args_list}
    assert called_names == {"alpha", "beta"}
