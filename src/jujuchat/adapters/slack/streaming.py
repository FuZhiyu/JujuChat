"""Streaming message handler for Slack interim updates during long-running tasks."""

import asyncio
import time
from typing import Dict, Any, Optional
from datetime import datetime


class SlackStreamHandler:
    """Handles streaming updates to Slack during long-running Claude operations.

    This class manages interim message updates, showing progress and partial
    responses as Claude processes the request.
    """

    def __init__(
        self,
        client,
        channel: str,
        thread_ts: Optional[str] = None,
        min_update_interval: float = 2.0,  # Minimum seconds between updates
        show_partial_text: bool = True,
    ):
        """Initialize the stream handler.

        Args:
            client: Slack client for API calls
            channel: Channel ID to post messages to
            thread_ts: Optional thread timestamp for threaded responses
            min_update_interval: Minimum seconds between message updates (rate limiting)
            show_partial_text: Whether to show partial text as it streams
        """
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.min_update_interval = min_update_interval
        self.show_partial_text = show_partial_text

        # State tracking
        self.message_ts: Optional[str] = None
        self.last_update_time: float = 0
        self.accumulated_text: str = ""
        self.current_status: str = "thinking"
        self.tool_calls: list = []
        self.pending_update: bool = False
        self._update_lock = asyncio.Lock()
        self.interrupted: bool = False
        self._interrupt_reason: Optional[str] = None

    async def initialize(self) -> None:
        """Post the initial 'thinking' message to Slack."""
        try:
            response = await self.client.chat_postMessage(
                channel=self.channel,
                text=":thought_balloon: Thinking...",
                thread_ts=self.thread_ts,
            )
            self.message_ts = response["ts"]
            self.last_update_time = time.time()
        except Exception as e:
            print(f"Failed to post initial message: {e}")
            # Continue without interim updates if this fails

    async def handle_event(self, event: Dict[str, Any]) -> None:
        """Handle a streaming event from Claude SDK.

        Args:
            event: Event dictionary from Claude SDK
        """
        if not self.message_ts:
            # If we couldn't post initial message, skip updates
            return
        # If interrupted, ignore further streaming events
        if self.interrupted:
            return

        event_type = event.get("type")

        try:
            if event_type == "AssistantMessage":
                # Handle partial text from assistant
                text_chunk = event.get("text", "")
                if text_chunk and self.show_partial_text:
                    self.accumulated_text += text_chunk
                    await self._schedule_update()

            elif event_type == "SystemMessage":
                # Handle system messages (tool calls, status updates)
                subtype = event.get("subtype")
                if subtype == "tool_use_start":
                    tool_name = event.get("data", {}).get("name", "tool")
                    self.current_status = f"Using {tool_name}..."
                    await self._schedule_update()
                elif subtype == "tool_use_end":
                    self.current_status = "processing"
                    await self._schedule_update()

            elif event_type == "ResultMessage":
                # Final result - we can stop updating
                self.current_status = "complete"

        except Exception as e:
            print(f"Error handling stream event: {e}")

    async def _schedule_update(self) -> None:
        """Schedule a message update, respecting rate limits."""
        if self.interrupted:
            return
        async with self._update_lock:
            current_time = time.time()
            time_since_last = current_time - self.last_update_time

            if time_since_last >= self.min_update_interval:
                # Enough time has passed, update now
                await self._update_message()
                self.pending_update = False
            else:
                # Too soon, mark as pending
                if not self.pending_update:
                    self.pending_update = True
                    # Schedule delayed update
                    delay = self.min_update_interval - time_since_last
                    asyncio.create_task(self._delayed_update(delay))

    async def _delayed_update(self, delay: float) -> None:
        """Perform a delayed update after the specified delay.

        Args:
            delay: Seconds to wait before updating
        """
        await asyncio.sleep(delay)
        async with self._update_lock:
            if self.interrupted:
                return
            if self.pending_update:
                await self._update_message()
                self.pending_update = False

    async def _update_message(self) -> None:
        """Update the Slack message with current progress."""
        if not self.message_ts:
            return
        if self.interrupted:
            return

        try:
            # Build the interim message
            parts = []

            # Status indicator
            status_icons = {
                "thinking": ":thought_balloon:",
                "processing": ":gear:",
                "complete": ":white_check_mark:",
                "interrupted": ":stop_sign:",
            }
            icon = status_icons.get(self.current_status, ":hourglass:")

            if self.current_status == "complete":
                parts.append(f"{icon} Complete")
            elif self.current_status == "interrupted":
                reason = f" — {self._interrupt_reason}" if self._interrupt_reason else ""
                parts.append(f"{icon} Interrupted{reason}")
            elif self.current_status.startswith("Using"):
                parts.append(f":wrench: {self.current_status}")
            else:
                parts.append(f"{icon} Working...")

            # Show partial text if available and enabled
            if self.accumulated_text and self.show_partial_text:
                # Truncate if too long
                display_text = self.accumulated_text
                # Convert literal \n sequences to actual newlines for Slack display
                try:
                    display_text = display_text.replace("\\n", "\n")
                except Exception:
                    pass
                if len(display_text) > 2000:
                    display_text = display_text[:2000] + "\n\n_(still processing...)_"
                parts.append(f"\n\n{display_text}")

            message_text = "".join(parts)

            # Update the message
            await self.client.chat_update(
                channel=self.channel,
                ts=self.message_ts,
                text=message_text,
            )
            self.last_update_time = time.time()

        except Exception as e:
            print(f"Failed to update message: {e}")

    async def finalize(self, final_text: str) -> None:
        """Replace the interim message with the final response.

        Args:
            final_text: The final formatted response text
        """
        if not self.message_ts:
            # If we never posted an interim message, caller will post the final message
            return
        if self.interrupted:
            # If we never posted an interim message, caller will post the final message
            return

        try:
            # Normalize any literal \n sequences before finalizing
            try:
                final_text = final_text.replace("\\n", "\n")
            except Exception:
                pass
            # Replace interim message with final result
            await self.client.chat_update(
                channel=self.channel,
                ts=self.message_ts,
                text=final_text,
            )
        except Exception as e:
            print(f"Failed to finalize message: {e}")
            # If update fails, we'll let the caller post a new message
            raise

    def get_message_ts(self) -> Optional[str]:
        """Get the timestamp of the interim message.

        Returns:
            Message timestamp if a message was posted, None otherwise
        """
        return self.message_ts

    async def mark_interrupted(self, reason: Optional[str] = None) -> None:
        """Mark the interim message as interrupted and update Slack.

        Args:
            reason: Optional text to append to the status
        """
        self.interrupted = True
        self._interrupt_reason = reason
        self.current_status = "interrupted"
        if not self.message_ts:
            return
        try:
            reason_text = f" — {reason}" if reason else ""
            await self.client.chat_update(
                channel=self.channel,
                ts=self.message_ts,
                text=f":stop_sign: Interrupted{reason_text}",
            )
        except Exception as e:
            print(f"Failed to mark message as interrupted: {e}")
