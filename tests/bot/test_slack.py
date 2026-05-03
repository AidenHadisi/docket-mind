"""Tests for the Slack platform adapter."""

from unittest.mock import AsyncMock, patch

import pytest

from docketmind.chat import SourceChunk
from docketmind.commands import CommandParam, CommandSpec
from docketmind.platforms import BotResponse, PermissionLevel, PlatformNotConfigured
from docketmind.platforms.slack import SlackPlatform, _truncate


def test_raises_not_configured_when_tokens_missing(monkeypatch):
    """SlackPlatform raises PlatformNotConfigured if tokens are absent."""
    monkeypatch.setattr("docketmind.platforms.slack.settings.slack_bot_token", "")
    monkeypatch.setattr("docketmind.platforms.slack.settings.slack_app_token", "")
    with pytest.raises(PlatformNotConfigured):
        SlackPlatform()


def test_raises_not_configured_when_app_token_missing(monkeypatch):
    """SlackPlatform requires both tokens."""
    monkeypatch.setattr("docketmind.platforms.slack.settings.slack_bot_token", "xoxb-test")
    monkeypatch.setattr("docketmind.platforms.slack.settings.slack_app_token", "")
    with pytest.raises(PlatformNotConfigured):
        SlackPlatform()


def test_parse_args_splits_text_positionally():
    """Args are assigned positionally from the slash command text."""
    spec = CommandSpec(
        name="ask",
        description="Ask",
        handler=AsyncMock(),
        params=[
            CommandParam("question", str, "The question", True),
            CommandParam("case_id", str, "Case ID", False),
        ],
    )
    args = SlackPlatform._parse_args(spec, "what happened 12345")
    assert args == {"question": "what", "case_id": "happened"}


def test_parse_args_missing_optional_is_none():
    """Missing optional args default to None."""
    spec = CommandSpec(
        name="ask",
        description="Ask",
        handler=AsyncMock(),
        params=[
            CommandParam("question", str, "The question", True),
            CommandParam("case_id", str, "Case ID", False),
        ],
    )
    args = SlackPlatform._parse_args(spec, "hello")
    assert args == {"question": "hello", "case_id": None}


def test_parse_args_empty_text():
    """Empty command text gives empty-string for required params and None for optional."""
    spec = CommandSpec(
        name="ask",
        description="Ask",
        handler=AsyncMock(),
        params=[
            CommandParam("question", str, "The question", True),
            CommandParam("case_id", str, "Case ID", False),
        ],
    )
    args = SlackPlatform._parse_args(spec, "")
    assert args == {"question": "", "case_id": None}


def test_channel_id_encoding():
    """channel_id combines team and channel."""
    cmd = {"team_id": "T123", "channel_id": "C456"}
    assert SlackPlatform._channel_id(cmd) == "T123:C456"


def test_permission_level_defaults_to_user():
    """Without admin info, permission defaults to USER."""
    cmd = {"user_id": "U1"}
    assert SlackPlatform._permission_level(cmd) == PermissionLevel.USER


def test_source_label_pdf_url():
    """PDF sources link to the PDF."""
    src = SourceChunk(text="x", score=0.9, type="pdf", pdf_url="https://example.com/doc.pdf")
    label = SlackPlatform._source_label(src)
    assert label == "<https://example.com/doc.pdf|doc.pdf>"


def test_source_label_court_listener_url():
    """Docket entries link to CourtListener."""
    src = SourceChunk(
        text="x",
        score=0.8,
        type="docket_entry",
        title="Motion to Dismiss",
        court_listener_id="https://www.courtlistener.com/docket/123/45/",
    )
    label = SlackPlatform._source_label(src)
    assert label == "<https://www.courtlistener.com/docket/123/45/|Motion to Dismiss>"


def test_source_label_plain_title():
    """Non-URL court_listener_id just returns the title."""
    src = SourceChunk(
        text="x",
        score=0.7,
        type="docket_entry",
        title="Some Entry",
        court_listener_id="12345",
    )
    label = SlackPlatform._source_label(src)
    assert label == "Some Entry"


def test_build_blocks_includes_header_and_section():
    """A response with a question produces a header block + section."""
    response = BotResponse(text="The answer.", question="What happened?")
    blocks = SlackPlatform._build_blocks(response)
    assert blocks[0]["type"] == "header"
    assert blocks[0]["text"]["text"] == "What happened?"
    assert blocks[1]["type"] == "section"
    assert "The answer." in blocks[1]["text"]["text"]


def test_build_blocks_includes_sources():
    """Citations produce a divider + context block."""
    response = BotResponse(
        text="Answer.",
        citations=[
            SourceChunk(
                text="chunk",
                score=0.9,
                type="docket_entry",
                title="Doc A",
                date_filed="2026-01-15",
            ),
        ],
    )
    blocks = SlackPlatform._build_blocks(response)
    types = [b["type"] for b in blocks]
    assert "divider" in types
    assert "context" in types
    context = next(b for b in blocks if b["type"] == "context")
    assert "Doc A" in context["elements"][0]["text"]
    assert "Jan 15, 2026" in context["elements"][0]["text"]


def test_build_blocks_no_question_skips_header():
    """Without a question, no header block is emitted."""
    response = BotResponse(text="Plain answer.")
    blocks = SlackPlatform._build_blocks(response)
    assert all(b["type"] != "header" for b in blocks)


def test_truncate_short_text_unchanged():
    """Short text passes through."""
    assert _truncate("hello", 100) == "hello"


def test_truncate_long_text_adds_ellipsis():
    """Long text gets truncated with ellipsis."""
    text = "word " * 100
    result = _truncate(text, 30)
    assert len(result) <= 30
    assert result.endswith("...")


@patch("docketmind.platforms.slack.settings")
async def test_send_uses_response_url_when_available(mock_settings):
    """send() should use the response_url webhook for replies."""
    mock_settings.slack_bot_token = "xoxb-test"
    mock_settings.slack_app_token = "xapp-test"

    platform = SlackPlatform()
    channel_id = "T1:C1"
    command_payload = {
        "team_id": "T1",
        "channel_id": "C1",
        "user_id": "U1",
        "response_url": "https://hooks.slack.com/commands/T1/test",
    }
    platform._pending[channel_id] = [command_payload]

    response = BotResponse(text="Done!")

    with patch("slack_sdk.webhook.async_client.AsyncWebhookClient") as MockWebhook:
        mock_instance = AsyncMock()
        MockWebhook.return_value = mock_instance
        await platform.send(channel_id, response)
        mock_instance.send.assert_awaited_once()
        call_kwargs = mock_instance.send.call_args[1]
        assert call_kwargs["text"] == "Done!"
        assert call_kwargs["response_type"] == "in_channel"
