"""Test script for Slack streaming functionality."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jujuchat.adapters.slack.streaming import SlackStreamHandler


async def test_stream_handler_initialization():
    """Test that stream handler can be initialized and post initial message."""
    print("Testing stream handler initialization...")

    # Mock Slack client
    mock_client = AsyncMock()
    mock_client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1234567890.123456"
    }

    # Create handler
    handler = SlackStreamHandler(
        client=mock_client,
        channel="C123456",
        thread_ts=None,
        min_update_interval=1.0,
        show_partial_text=True
    )

    # Initialize (should post thinking message)
    await handler.initialize()

    # Verify initial message was posted
    assert mock_client.chat_postMessage.called
    call_args = mock_client.chat_postMessage.call_args
    assert call_args.kwargs["channel"] == "C123456"
    assert "Thinking" in call_args.kwargs["text"]

    # Verify message_ts was stored
    assert handler.get_message_ts() == "1234567890.123456"

    print("✅ Initialization test passed")


async def test_stream_handler_events():
    """Test that stream handler can handle events and update messages."""
    print("Testing stream handler event handling...")

    # Mock Slack client
    mock_client = AsyncMock()
    mock_client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1234567890.123456"
    }
    mock_client.chat_update.return_value = {"ok": True}

    # Create handler
    handler = SlackStreamHandler(
        client=mock_client,
        channel="C123456",
        min_update_interval=0.1,  # Short interval for testing
        show_partial_text=True
    )

    await handler.initialize()

    # Simulate assistant message event
    event1 = {
        "type": "AssistantMessage",
        "text": "Hello, I am processing your request..."
    }
    await handler.handle_event(event1)

    # Wait for update to be scheduled
    await asyncio.sleep(0.2)

    # Verify update was called
    assert mock_client.chat_update.called
    update_args = mock_client.chat_update.call_args
    assert "Hello" in update_args.kwargs["text"]

    print("✅ Event handling test passed")


async def test_stream_handler_finalization():
    """Test that stream handler can finalize with final response."""
    print("Testing stream handler finalization...")

    # Mock Slack client
    mock_client = AsyncMock()
    mock_client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1234567890.123456"
    }
    mock_client.chat_update.return_value = {"ok": True}

    # Create handler
    handler = SlackStreamHandler(
        client=mock_client,
        channel="C123456",
    )

    await handler.initialize()

    # Finalize with final response
    final_response = "Here is the complete answer to your question."
    await handler.finalize(final_response)

    # Verify final update was called
    assert mock_client.chat_update.called
    final_args = mock_client.chat_update.call_args
    assert final_args.kwargs["text"] == final_response
    assert final_args.kwargs["ts"] == "1234567890.123456"

    print("✅ Finalization test passed")


async def test_tool_use_events():
    """Test handling of tool use events."""
    print("Testing tool use event handling...")

    # Mock Slack client
    mock_client = AsyncMock()
    mock_client.chat_postMessage.return_value = {
        "ok": True,
        "ts": "1234567890.123456"
    }
    mock_client.chat_update.return_value = {"ok": True}

    # Create handler
    handler = SlackStreamHandler(
        client=mock_client,
        channel="C123456",
        min_update_interval=0.1,
    )

    await handler.initialize()

    # Simulate tool use start event
    tool_event = {
        "type": "SystemMessage",
        "subtype": "tool_use_start",
        "data": {"name": "Read"}
    }
    await handler.handle_event(tool_event)

    # Wait for update
    await asyncio.sleep(0.2)

    # Verify status was updated
    assert mock_client.chat_update.called
    update_text = mock_client.chat_update.call_args.kwargs["text"]
    assert "Read" in update_text

    print("✅ Tool use event test passed")


async def main():
    """Run all tests."""
    print("=" * 50)
    print("Running Slack Streaming Handler Tests")
    print("=" * 50)

    try:
        await test_stream_handler_initialization()
        await test_stream_handler_events()
        await test_stream_handler_finalization()
        await test_tool_use_events()

        print("\n" + "=" * 50)
        print("✅ All tests passed!")
        print("=" * 50)
        return 0

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
