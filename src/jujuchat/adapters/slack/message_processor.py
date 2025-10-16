#%% Message Processor
"""
Message processing module for the Slack Claude Bot.

This module handles incoming messages from Slack, processes them through
Claude Code, and formats responses for Slack display.
"""

import re
from typing import Dict, Optional, List
from datetime import datetime

# PersistentClaudeLocal no longer needed - using ChatBackend directly
from .logger import BotLogger
from .config import BotConfig
from .exceptions import MessageProcessingError, ClaudeError, LoggingError
from .streaming import SlackStreamHandler
from cachetools import TTLCache

class MessageProcessor:
    """
    Processes messages between Slack and Claude Code.
    
    This class handles message routing, command processing, session management,
    and response formatting for optimal Slack display.
    """
    
    def __init__(self, claude_backend, logger: BotLogger, config: BotConfig):
        """
        Initialize MessageProcessor with dependencies.
        
        Args:
            claude_backend: ChatBackend integration instance
            logger: Bot logger instance
            config: Complete bot configuration
        """
        self.claude = claude_backend
        self.logger = logger
        self.config = config
        self.conversation_sessions: Dict[str, str] = {}  # Track sessions per channel
        # Cache for attachment-first messages (session_id -> {paths, timestamp})
        self.pending_attachments = TTLCache(maxsize=100, ttl=60)
        # Auto-compact tracking
        self._auto_compact_sessions: set[str] = set()
        # Track active Slack stream handlers per session for interruption support
        self._active_streams: Dict[str, SlackStreamHandler] = {}
        
    async def process_message(
        self,
        text: str,
        channel: str,
        user_name: str,
        user_id: str = None,
        user_timezone: Optional[str] = None,
        attachment_paths: Optional[List[str]] = None,
        slack_client = None,
        thread_ts: Optional[str] = None,
    ) -> tuple[str, Optional[str]]:
        """
        Process incoming message and return response.

        Args:
            text: Message text from user
            channel: Slack channel ID
            user_name: User's display name for Claude context
            user_id: Slack user ID for logging (optional for backward compatibility)
            attachment_paths: Optional list of file paths for attachments
            slack_client: Optional Slack client for streaming interim updates
            thread_ts: Optional thread timestamp for interim messages

        Returns:
            Tuple of (formatted response string, message_ts of interim message if posted)

        Raises:
            MessageProcessingError: When message processing fails
        """
        stream_handler_obj = None
        try:
            # Check for special commands first
            if self._is_command(text):
                result = await self._handle_command(text, channel, user_id)
                return (result, None)
            
            # Process regular message through Claude
            session_id = f"slack_{channel}"
            
            # Update session metadata with current thread for default tool behavior (e.g., uploads)
            try:
                self.claude.update_session_metadata(session_id, thread_ts=thread_ts, user_timezone=user_timezone)
            except Exception:
                pass

            # Update session timestamp
            timestamp = datetime.now().isoformat()
            self.conversation_sessions[session_id] = timestamp
            
            # Merge any cached attachments if present
            paths = attachment_paths or []
            if session_id in self.pending_attachments:
                cached = self.pending_attachments.pop(session_id)
                paths = (paths or []) + cached.get('paths', [])

            # Add user context to message for Claude
            if user_timezone:
                contextual_message = f"User: {user_name}\nTimezone: {user_timezone}\nMessage: {text}"
            else:
                contextual_message = f"User: {user_name}\nMessage: {text}"

            # Handle attachment-only messages similar to HTTP server logic
            trimmed_text = (text or "").strip()
            if not trimmed_text and paths:
                # Simple audio detection by extension
                audio_exts = {'.mp3', '.wav', '.m4a', '.ogg', '.aac', '.flac', '.opus', '.amr', '.3gp'}
                audio_files = [p for p in paths if any(p.lower().endswith(ext) for ext in audio_exts)]
                if audio_files:
                    audio_list = ", ".join(audio_files)
                    contextual_message = (
                        f"User: {user_name}\n"
                        f"Message: User sent audio message(s) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}."
                        f" Files: {audio_list}. Use whisper_transcribe tool to transcribe and respond."
                    )
                else:
                    # Cache attachments and prompt for a message
                    self.pending_attachments[session_id] = {
                        'paths': paths,
                        'timestamp': datetime.now(),
                    }
                    return ("Attachment received, waiting for your message...", None)
            
            # Preface with attachment paths if available and permitted
            preface = ""
            if paths:
                joined = ", ".join(paths)
                preface = f"Attachments available on disk (readable via file tools): {joined}\n\n"

            # Create stream handler for interim updates if Slack client provided
            stream_handler = None
            interim_message_ts = None
            if slack_client:
                stream_handler_obj = SlackStreamHandler(
                    client=slack_client,
                    channel=channel,
                    thread_ts=thread_ts,
                    min_update_interval=2.0,
                    show_partial_text=True,
                )
                await stream_handler_obj.initialize()
                stream_handler = stream_handler_obj.handle_event
                interim_message_ts = stream_handler_obj.get_message_ts()
                # Track active stream handler for this session to allow !interrupt to update the message
                try:
                    self._active_streams[session_id] = stream_handler_obj
                except Exception:
                    pass

            # Send to Claude Code with session context and streaming
            response = await self.claude.send_message_with_session(
                (preface + contextual_message) if preface else contextual_message,
                session_id,
                stream_handler=stream_handler,
            )

            # Format response for Slack with channel-specific settings
            formatted_response = self._format_for_slack(response, channel)

            # Update interim message with final response if one was posted
            if slack_client and interim_message_ts:
                try:
                    await stream_handler_obj.finalize(formatted_response)
                except Exception as e:
                    print(f"Failed to finalize interim message, will post new message: {e}")
                    # If finalization fails, return None to signal caller should post new message
                    interim_message_ts = None

            return (formatted_response, interim_message_ts)
            
        except ClaudeError as e:
            error_msg = f"Claude Code error: {str(e)}"
            await self._safe_log_error(user_id, channel, error_msg)
            return (f"I encountered an issue with Claude Code: {str(e)}", None)
        except Exception as e:
            error_msg = f"Unexpected error in message processing: {str(e)}"
            await self._safe_log_error(user_id, channel, error_msg)
            return (f"I encountered an unexpected error: {str(e)}", None)
        finally:
            # Clean up active stream tracking once processing completes or errors
            try:
                if stream_handler_obj is not None:
                    if self._active_streams.get(session_id) is stream_handler_obj:
                        del self._active_streams[session_id]
            except Exception:
                pass
    
    def _is_command(self, text: str) -> bool:
        """Check if the message is a bot command."""
        return text.strip().lower().startswith('!')
    
    async def _handle_command(self, text: str, channel: str, user_id: str) -> str:
        """
        Handle special bot commands.
        
        Args:
            text: Command text
            channel: Slack channel ID
            user_id: Slack user ID
            
        Returns:
            Command response
        """
        command = text.strip().lower()
        
        if command == "!help":
            return self._get_help_message()
        
        elif command == "!reset":
            return await self._handle_reset_command(channel)
        
        elif command == "!status":
            return await self._handle_status_command(channel)

        elif command == "!interrupt":
            return await self._handle_interrupt_command(channel)

        elif command.startswith("!history"):
            return await self._handle_history_command(channel, command)
        
        elif command == "!config":
            return await self._handle_config_command(channel)
        
        elif command == "!reload-config":
            return await self._handle_reload_config_command()
        
        elif command == "!schedule" or command.startswith("!schedule "):
            return await self._handle_schedule_command(command, channel)

        elif command.startswith("!compact"):
            return await self._handle_compact_command(channel)

        elif command.startswith("!auto-compact"):
            return await self._handle_auto_compact_command(channel, command)

        else:
            return f"Unknown command: {command}. Type `!help` for available commands."
    
    async def _handle_reset_command(self, channel: str) -> str:
        """Handle conversation reset command."""
        session_id = f"slack_{channel}"

        # Reset both local session tracking and persistent Claude session
        if session_id in self.conversation_sessions:
            del self.conversation_sessions[session_id]

        # Clear auto-compact state for this session
        self._auto_compact_sessions.discard(session_id)

        # Reset the persistent Claude session
        await self.claude.reset_session(session_id)

        return "✅ Conversation context has been reset."

    async def _handle_interrupt_command(self, channel: str) -> str:
        """Handle interrupt command to stop the current long-running operation."""
        try:
            session_id = f"slack_{channel}"
            await self.claude.interrupt_session(session_id)
            # Update any active interim message to show interrupted state
            handler = self._active_streams.get(session_id)
            if handler:
                try:
                    await handler.mark_interrupted("requested by user")
                except Exception:
                    pass
            return "🛑 Attempting to interrupt the current operation."
        except Exception as e:
            return f"❌ Failed to interrupt: {str(e)}"
    
    async def _handle_status_command(self, channel: str) -> str:
        """Handle status command."""
        try:
            stats = self.logger.get_log_stats()
            session_id = f"slack_{channel}"
            session_active = session_id in self.conversation_sessions
            active_sessions = self.claude.get_active_sessions()
            channel_config = self.config.get_channel_config(channel)
            
            status_text = f"""🤖 **Slack Claude Bot Status**

📊 **Statistics:**
• Active session: {'Yes' if session_active else 'No'}
• Persistent Claude sessions: {len(active_sessions)}
• Today's conversations: {stats['conversations']}
• Today's errors: {stats['errors']}
• Total configured channels: {len(self.config.channels)}

⚙️ **Configuration:**
• Project root: `{channel_config.project_root}`
• Claude command: `{channel_config.claude_command}`
• Claude model: `{channel_config.claude_model or 'default'}`
• Max response length: {channel_config.max_response_length} chars"""
            
            # Add channel-specific info
            if channel in self.config.channels:
                status_text += "\n• Channel config: ✅ Custom settings active"
            else:
                status_text += "\n• Channel config: 📋 Using global settings"
            
            status_text += "\n\n💡 **Available Commands:**\n• `!help` - Show available commands\n• `!reset` - Clear conversation context\n• `!interrupt` - Interrupt a long-running response\n• `!status` - Show this status\n• `!config` - Show channel configuration\n• `!history [N]` - Show last N messages\n• `!compact` - Compact conversation history\n• `!auto-compact [on|off|status]` - Manage automatic history compaction\n• `!reload-config` - Reload configuration from file\n• `!sendfile <path ...>` - Upload file(s) from session attachments"
            
            return status_text
        
        except Exception as e:
            return f"Error retrieving status: {str(e)}"    
    
    async def _handle_history_command(self, channel: str, command: str) -> str:
        """Handle conversation history command."""
        try:
            # Parse limit from command (e.g., "!history 5")
            parts = command.split()
            limit = 5  # default
            if len(parts) > 1:
                try:
                    limit = int(parts[1])
                    limit = max(1, min(limit, 20))  # Clamp between 1 and 20
                except ValueError:
                    return "Invalid limit. Use: `!history [number]` (1-20)"
            
            history = await self.logger.get_conversation_history(channel, limit)
            
            if not history:
                return "No conversation history found for this channel."
            
            # Format history for display
            formatted_history = "*Recent Conversation History:*\n"
            for entry in history[-limit:]:
                timestamp = entry['timestamp'][:19]  # Remove microseconds
                msg_type = entry['type']
                message = entry['message'][:100]  # Truncate long messages
                if len(entry['message']) > 100:
                    message += "..."
                formatted_history += f"• {timestamp} ({msg_type}): {message}\n"
            
            return formatted_history
            
        except LoggingError as e:
            return f"Error retrieving history: {str(e)}"
        except Exception as e:
            return f"Unexpected error retrieving history: {str(e)}"
    
    async def _handle_config_command(self, channel: str) -> str:
        """Handle channel configuration display command."""
        try:
            channel_config = self.config.get_channel_config(channel)
            
            config_text = f"""⚙️ **Channel Configuration ({channel})**

🔧 **Claude Settings:**
• Model: `{channel_config.claude_model or 'default'}`
• System prompt: {'Custom' if channel in self.config.channels and self.config.channels[channel].system_prompt else 'Global'}
• Max response length: {channel_config.max_response_length} chars
• Verbose mode: {'Yes' if channel_config.claude_verbose else 'No'}
• Allowed tools: `{channel_config.claude_allowed_tools or 'all'}`
• Disallowed tools: `{channel_config.claude_disallowed_tools or 'none'}`
• Additional directories: `{channel_config.claude_add_dirs or 'none'}`

📁 **Paths:**
• Project root: `{channel_config.project_root}`
• Log directory: `{channel_config.log_dir}`
• Claude command: `{channel_config.claude_command}`
"""
            
            if channel in self.config.channels:
                config_text += "\n✅ This channel has custom configuration settings."
            else:
                config_text += "\n📋 This channel uses global configuration settings."
            
            return config_text
            
        except Exception as e:
            return f"Error retrieving channel configuration: {str(e)}"
    
    async def _handle_reload_config_command(self) -> str:
        """Handle configuration reload command."""
        try:
            from .config import reload_config
            new_config = reload_config()
            # Update our reference to the new config
            self.config = new_config
            # Update claude's config reference too
            self.claude.bot_config = new_config
            return "✅ Configuration reloaded successfully from file!"
        except Exception as e:
            return f"❌ Failed to reload configuration: {str(e)}"
    
    async def _handle_schedule_command(self, command: str, channel: str) -> str:
        """
        Handle scheduler-related commands.
        
        Args:
            command: Full command string
            channel: Slack channel ID
            
        Returns:
            Command response
        """
        try:
            # Import scheduler here to avoid circular imports
            import slack_bot
            
            # Check if scheduler is available
            if not hasattr(slack_bot, 'scheduler') or slack_bot.scheduler is None:
                return "❌ Scheduler is not initialized or no scheduled messages are configured."
            
            scheduler = slack_bot.scheduler
            parts = command.split()
            
            if len(parts) == 1:  # Just "!schedule"
                # Show schedule status
                status = scheduler.get_schedule_status()
                
                status_text = f"""📅 **Scheduled Messages Status**

🔄 **Scheduler State:**
• Running: {'Yes' if status['running'] else 'No'}
• Total messages: {status['total_messages']}
• Enabled messages: {status['enabled_messages']}

📋 **Configured Messages:**"""
                
                if not status['messages']:
                    status_text += "\n• No scheduled messages configured"
                else:
                    for name, msg_info in status['messages'].items():
                        enabled_icon = "✅" if msg_info['enabled'] else "❌"
                        next_run = msg_info['next_run']
                        if next_run:
                            next_run_str = next_run.replace('T', ' ').split('.')[0]  # Format datetime
                        else:
                            next_run_str = "Not scheduled"
                        
                        status_text += f"\n• {enabled_icon} `{name}` -> {msg_info['channel']}"
                        status_text += f"\n  ⏰ Next run: {next_run_str}"
                        status_text += f"\n  📋 Cron: `{msg_info['cron_expression']}`"
                
                status_text += f"\n\n💡 **Commands:**\n• `!schedule` - Show this status\n• `!schedule enable <name>` - Enable a scheduled message\n• `!schedule disable <name>` - Disable a scheduled message"
                
                return status_text
            
            elif len(parts) >= 3:  # Commands with arguments
                action = parts[1].lower()
                name = parts[2]
                
                if action == "enable":
                    success = await scheduler.enable_scheduled_message(name)
                    if success:
                        return f"✅ Enabled scheduled message: `{name}`"
                    else:
                        return f"❌ Scheduled message not found: `{name}`"
                
                elif action == "disable":
                    success = await scheduler.disable_scheduled_message(name)
                    if success:
                        return f"✅ Disabled scheduled message: `{name}`"
                    else:
                        return f"❌ Scheduled message not found: `{name}`"
                
                else:
                    return f"❌ Unknown schedule action: `{action}`. Use `enable` or `disable`."
            
            else:
                return "❌ Invalid schedule command. Use `!schedule` for status or `!schedule <enable|disable> <name>`."
                
        except Exception as e:
            return f"❌ Error handling schedule command: {str(e)}"

    async def _handle_compact_command(self, channel: str) -> str:
        """Manually compact the session conversation history."""
        session_id = f"slack_{channel}"
        try:
            result = await self.claude.compact_session(session_id)
            if result is False:
                return "❌ Compact command not supported by the backend."
            return "🧹 Conversation compacted."
        except Exception as exc:
            return f"❌ Error compacting conversation: {str(exc)}"

    async def _handle_auto_compact_command(self, channel: str, command: str) -> str:
        """Toggle or report auto-compact state for a channel session."""
        session_id = f"slack_{channel}"
        parts = command.split()

        # Status check
        if len(parts) == 1:
            enabled = session_id in self._auto_compact_sessions
            return f"🤖 Auto-compact is currently {'enabled' if enabled else 'disabled'}."

        # Enable/disable
        if len(parts) >= 2:
            action = parts[1].lower()
            if action == "on":
                self._auto_compact_sessions.add(session_id)
                try:
                    await self.claude.compact_session(session_id, auto=True)
                except Exception as exc:
                    return f"⚠️ Auto-compact enabled, but initial compact failed: {str(exc)}"
                return "✅ Auto-compact enabled for this channel."
            elif action == "off":
                self._auto_compact_sessions.discard(session_id)
                try:
                    await self.claude.compact_session(session_id, auto=False)
                except Exception:
                    pass  # Ignore errors when disabling
                return "✅ Auto-compact disabled for this channel."

        return "❌ Invalid auto-compact command. Use `!auto-compact on`, `!auto-compact off`, or `!auto-compact status`."

    def _format_for_slack(self, text: str, channel: str = None) -> str:
        """
        Format Claude's response for optimal Slack display.
        
        Converts standard Markdown to Slack's mrkdwn format:
        - Headers: Convert # headers to bold text
        - Lists: Convert - or * lists to simple bullet points
        - Links: Convert [text](url) to <url|text>
        - Tables: Strip unsupported table formatting
        - Code blocks: Remove language specifiers
        
        Args:
            text: Raw response from Claude Code
            channel: Channel ID for getting channel-specific max length
            
        Returns:
            Formatted text suitable for Slack mrkdwn
        """
        formatted_text = text
        
        # Convert headers to bold text (Slack doesn't support multiple header levels)
        # Convert ### Header to *Header*
        formatted_text = re.sub(r'^#{1,6}\s*(.+)$', r'*\1*', formatted_text, flags=re.MULTILINE)
        
        # Convert standard markdown bold **text** to Slack bold *text*
        formatted_text = re.sub(r'\*\*([^*]+)\*\*', r'*\1*', formatted_text)
        
        # Convert standard markdown links [text](url) to Slack format <url|text>
        formatted_text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<\2|\1>', formatted_text)
        
        # Convert markdown lists to simple text (Slack doesn't support markdown list syntax)
        # Convert "- item" or "* item" to "• item"
        formatted_text = re.sub(r'^[\s]*[-*]\s+(.+)$', r'• \1', formatted_text, flags=re.MULTILINE)
        
        # Convert numbered lists "1. item" to "1. item" (keep as-is, but could be "• item")
        formatted_text = re.sub(r'^[\s]*\d+\.\s+(.+)$', r'• \1', formatted_text, flags=re.MULTILINE)
        
        # Remove table formatting (Slack doesn't support tables)
        # Remove table header separators like |---|---|
        formatted_text = re.sub(r'^\|[\s\-:|]+\|$', '', formatted_text, flags=re.MULTILINE)
        
        # Convert table rows to simple lines (remove | delimiters)
        # This is a basic conversion - tables will become plain text
        formatted_text = re.sub(r'^\|(.+)\|$', r'\1', formatted_text, flags=re.MULTILINE)
        formatted_text = re.sub(r'\s*\|\s*', ' | ', formatted_text)
        
        # Convert markdown code blocks to Slack format
        # Replace ```language with ``` (remove language specifier)
        formatted_text = re.sub(r'```(\w+)\n', r'```\n', formatted_text)
        
        # Clean up extra whitespace and empty lines
        formatted_text = re.sub(r'\n\s*\n\s*\n', '\n\n', formatted_text)
        formatted_text = formatted_text.strip()
        
        # Get channel-specific max length if available
        if channel:
            channel_config = self.config.get_channel_config(channel)
            max_length = channel_config.max_response_length
        else:
            max_length = self.config.app.max_response_length
        
        # Ensure response isn't too long for Slack
        if len(formatted_text) > max_length:
            formatted_text = (
                formatted_text[:max_length] + 
                "\n\n... (response truncated)"
            )
        
        return formatted_text
    
    def _get_help_message(self) -> str:
        """
        Return comprehensive help message for users.
        
        Returns:
            Formatted help message
        """
        return """🤖 **Claude Code Assistant - Help**

I can help you explore and understand this codebase with read-only access to:
• Browse and read files
• Search for code patterns  
• Check git history and status
• Explain code functionality

💡 **Available Commands:**
• `!help` - Show this help message
• `!reset` - Reset conversation context
• `!interrupt` - Interrupt a long-running response
• `!status` - Show bot status and statistics
• `!config` - Show channel configuration
• `!history [N]` - Show last N conversation messages (default: 5, max: 20)
• `!compact` - Compact the stored conversation history
• `!auto-compact [on|off|status]` - Manage automatic history compaction
• `!reload-config` - Reload configuration from file
• `!schedule` - Show scheduled messages status and controls
• `!sendfile <path ...>` - Upload file(s) from session attachments

❓ **Example Questions:**
• "What does the main.py file do?"
• "Find all functions that handle authentication"
• "Show me the recent git commits"
• "Explain how the database connection works"
• "What are the main dependencies in requirements.txt?"

Just ask me anything about the codebase! 🚀"""
    
    async def _safe_log_error(self, user_id: str, channel: str, error: str) -> None:
        """
        Safely log errors without raising exceptions.
        
        Args:
            user_id: Slack user ID
            channel: Slack channel ID
            error: Error message to log
        """
        try:
            await self.logger.log_error(user_id, channel, error)
        except Exception:
            # If logging fails, at least print to console
            print(f"Failed to log error for {user_id} in {channel}: {error}")
    
    def get_session_count(self) -> int:
        """Get the number of active sessions."""
        return len(self.conversation_sessions)
    
    async def cleanup_old_sessions(self, max_age_hours: int = 24) -> int:
        """
        Clean up old conversation sessions.
        
        Args:
            max_age_hours: Maximum age in hours before session cleanup
            
        Returns:
            Number of sessions cleaned up
        """
        from datetime import datetime, timedelta
        
        current_time = datetime.now()
        cutoff_time = current_time - timedelta(hours=max_age_hours)
        
        sessions_to_remove = []
        scheduler_sessions_to_remove = []
        
        for session_id, timestamp_str in self.conversation_sessions.items():
            try:
                session_time = datetime.fromisoformat(timestamp_str)
                
                # Special handling for scheduler sessions - clean more aggressively (1 hour)
                if "SYSTEM_SCHEDULER" in session_id:
                    scheduler_cutoff = current_time - timedelta(hours=1)
                    if session_time < scheduler_cutoff:
                        scheduler_sessions_to_remove.append(session_id)
                elif session_time < cutoff_time:
                    sessions_to_remove.append(session_id)
            except ValueError:
                # Invalid timestamp format, remove session
                sessions_to_remove.append(session_id)
        
        # Combine all sessions to remove
        all_sessions_to_remove = sessions_to_remove + scheduler_sessions_to_remove
        
        # Clean up both local sessions and persistent Claude sessions
        for session_id in all_sessions_to_remove:
            del self.conversation_sessions[session_id]
        
        # Reset persistent Claude sessions in batch
        for session_id in all_sessions_to_remove:
            await self.claude.reset_session(session_id)
        
        if scheduler_sessions_to_remove:
            print(f"🧹 Cleaned up {len(scheduler_sessions_to_remove)} scheduler sessions")
        
        return len(all_sessions_to_remove)
    
    async def cleanup_persistent_sessions(self) -> None:
        """Cleanup all persistent Claude sessions."""
        await self.claude.cleanup_all_sessions()
