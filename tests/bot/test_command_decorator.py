"""Tests for @command decorator, CommandSpec, CommandParam, and the registry."""

import pytest

from docketmind.commands import CommandParam, CommandSpec, command, get_specs
from docketmind.platforms import BotResponse, PermissionLevel, PlatformEvent


def test_command_spec_is_frozen():
    """CommandSpec instances are immutable."""
    specs = get_specs()
    assert specs[0].__class__.__dataclass_params__.frozen is True  # type: ignore[attr-defined]


def test_command_param_is_immutable():
    """CommandParam instances are immutable (NamedTuple)."""
    param = CommandParam("x", str, "desc")
    with pytest.raises(AttributeError):
        param.name = "y"  # type: ignore[misc]


def test_all_specs_have_unique_names():
    specs = get_specs()
    names = [s.name for s in specs]
    assert len(names) == len(set(names))


def test_all_specs_have_callable_handlers():
    for spec in get_specs():
        assert callable(spec.handler)


def test_spec_params_match_expected_types():
    """Every CommandParam.type is a concrete Python type."""
    for spec in get_specs():
        for param in spec.params:
            assert isinstance(param.type, type), f"{spec.name}.{param.name} has non-type type"


def test_known_specs_present():
    """The four core commands are registered."""
    names = {s.name for s in get_specs()}
    assert names >= {"ask", "add_case", "remove_case", "list_cases"}


def test_ask_spec_metadata():
    spec = next(s for s in get_specs() if s.name == "ask")
    assert spec.cooldown == 5.0
    assert spec.permission == PermissionLevel.USER
    param_names = [p.name for p in spec.params]
    assert "question" in param_names
    assert "case_id" in param_names


def test_add_case_spec_requires_admin():
    spec = next(s for s in get_specs() if s.name == "add_case")
    assert spec.permission == PermissionLevel.ADMIN
    assert spec.ephemeral_defer is True


def test_remove_case_spec_requires_admin():
    spec = next(s for s in get_specs() if s.name == "remove_case")
    assert spec.permission == PermissionLevel.ADMIN
    assert spec.ephemeral_defer is True


def test_list_cases_spec_has_no_params():
    spec = next(s for s in get_specs() if s.name == "list_cases")
    assert spec.params == []
    assert spec.permission == PermissionLevel.USER


def test_decorator_stamps_spec_on_function():
    """@command attaches __command_spec__ to the decorated function."""

    @command(name="_test_stamp", description="Test stamp")
    async def my_handler(event: PlatformEvent) -> BotResponse:
        return BotResponse(text="ok")

    assert hasattr(my_handler, "__command_spec__")
    assert my_handler.__command_spec__.name == "_test_stamp"  # type: ignore[attr-defined]


async def test_handler_is_invocable():
    """Sanity check that a spec's handler can be called with a PlatformEvent."""
    spec = CommandSpec(
        name="ping",
        description="Test",
        handler=_ping_handler,
    )
    event = PlatformEvent(
        command="ping",
        args={},
        channel_id="ch",
        user_id="u",
        permission_level=PermissionLevel.USER,
    )
    response = await spec.handler(event)
    assert response.text == "pong"


async def _ping_handler(event: PlatformEvent) -> BotResponse:
    return BotResponse(text="pong")
