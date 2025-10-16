import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import sys

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jujuchat.adapters.slack.message_processor import MessageProcessor


async def test_processor_includes_timezone_in_context():
    """MessageProcessor should include a Timezone line when provided in DMs."""
    # Mock Claude backend with spies
    class MockClaude:
        def __init__(self):
            self.updated = {}
            self.sent_messages = []

        def update_session_metadata(self, session_id, **kwargs):
            self.updated[session_id] = kwargs

        async def send_message_with_session(self, message, session_id, *, stream_handler=None):
            # Capture the exact message
            self.sent_messages.append((session_id, message))
            return message

    claude = MockClaude()
    logger = MagicMock()
    config = MagicMock()

    processor = MessageProcessor(claude, logger, config)

    # Call with user_timezone
    resp, interim = await processor.process_message(
        text="Hello there",
        channel="D123",
        user_name="Julie",
        user_id="U123",
        user_timezone="America/Chicago",
        slack_client=None,
        thread_ts=None,
    )

    # Verify the message sent to Claude includes timezone line
    assert claude.sent_messages, "No message captured"
    _sid, captured = claude.sent_messages[-1]
    assert "Timezone: America/Chicago" in captured
    assert resp  # Non-empty response
    assert interim is None


async def test_dm_handler_timezone_change_triggers_reset_and_notice():
    """DM handler should reset session and notify when timezone changes."""
    from jujuchat.adapters.slack import bot as slack_bot

    # Prepare globals
    slack_bot.logger = SimpleNamespace(
        log_message=AsyncMock(),
        log_error=AsyncMock(),
    )

    # Mock Claude backend on processor
    class MockClaude:
        def __init__(self):
            self._meta = {"slack_D1": {"user_timezone": "America/New_York"}}
            self.reset_called = False

        def get_session_metadata(self, session_id):
            return dict(self._meta.get(session_id, {}))

        def update_session_metadata(self, session_id, **kwargs):
            self._meta.setdefault(session_id, {}).update(kwargs)

        async def reset_session(self, session_id):
            self.reset_called = True

    mock_claude = MockClaude()

    # Mock processor with claude backend and simple process_message
    slack_bot.processor = SimpleNamespace(
        claude=mock_claude,
        cleanup_old_sessions=AsyncMock(return_value=0),
        process_message=AsyncMock(return_value=("OK", None)),
    )

    # Minimal config
    slack_bot.config = SimpleNamespace(
        app=SimpleNamespace(attachments_max_size_mb=25, attachments_allowed_types=None),
        slack=SimpleNamespace(bot_token="xoxb-test"),
    )

    # Mock Slack client
    mock_client = AsyncMock()
    mock_client.users_info.return_value = {
        "ok": True,
        "user": {"tz": "America/Chicago", "tz_offset": -18000},
    }

    # Mock downloader to bypass network
    async def fake_download_all_from_event_files(**kwargs):
        return ([], [])

    # Say mock to capture bot messages
    say = AsyncMock()

    event = {
        "channel_type": "im",
        "user": "U1",
        "channel": "D1",
        "text": "hello",
        "ts": "111.222",
        "files": [],
    }

    with patch("jujuchat.adapters.slack.bot.download_all_from_event_files", new=fake_download_all_from_event_files):
        await slack_bot.handle_dm_message(event, say=say, ack=AsyncMock(), client=mock_client)

    # Assert reset occurred due to tz change
    assert mock_claude.reset_called is True
    # First say should be the tz-change notice
    assert say.await_args_list, "No messages sent via say()"
    first_call = say.await_args_list[0]
    # 'text' is passed positionally in say()
    first_text = first_call.args[0] if first_call.args else first_call.kwargs.get("text", "")
    assert "Detected timezone change" in first_text
    # Processor called with updated timezone
    slack_bot.processor.process_message.assert_awaited()
    called_kwargs = slack_bot.processor.process_message.await_args.kwargs
    assert called_kwargs.get("user_timezone") == "America/Chicago"
