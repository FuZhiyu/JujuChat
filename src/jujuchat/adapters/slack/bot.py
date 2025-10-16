#%% Slack Claude Bot - Main Application
"""
Main application file for the Slack Claude Bot.

This module sets up the Slack application, initializes all components,
and handles Slack events (direct messages and app mentions).
"""

import asyncio
import os
import re
from datetime import datetime
from typing import Optional, Dict

from slack_bolt.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

from .config import load_config, validate_config, print_config_status
from .message_processor import MessageProcessor
from .logger import BotLogger
from .scheduler import AsyncScheduler
from .exceptions import BotError, ConfigurationError, SlackError
from .attachments import download_all_from_event_files, get_session_attachments_dir
from .sender import upload_local_file
from .upload_handler import SlackUploadHandler

# Import core backend directly
from ...core import ChatBackend, ConfigProvider
import json
from pathlib import Path

# Slack-specific config provider for ChatBackend
class _SlackConfigProvider(ConfigProvider):
    """Adapter that maps Slack BotConfig -> SessionConfig using session_id."""

    def __init__(self, bot_config):
        self._bot_config = bot_config

    def get_session_config(self, session_id: str):
        base_cfg = self._bot_config.get_channel_config(session_id)
        return _SlackSessionConfigWrapper(base_cfg, session_id)


class _SlackSessionConfigWrapper:
    """Wraps AppConfig to add derived fields for backend consumption."""

    def __init__(self, base_cfg, session_id: str):
        self._cfg = base_cfg
        self._session_id = session_id
        self._mcp_config_json = None
        self._allowed_tools = None
        self._permission_mode = None
        try:
            mcp_json = self._build_mcp_config_json()
            if mcp_json:
                self._mcp_config_json = json.dumps(mcp_json)
            self._allowed_tools = self._compute_allowed_tools()
            self._permission_mode = self._compute_permission_mode()
        except Exception:
            self._mcp_config_json = None
            self._allowed_tools = None
            self._permission_mode = None

    def __getattr__(self, name):
        return getattr(self._cfg, name)

    @property
    def mcp_config_json(self) -> str | None:
        return self._mcp_config_json

    @property
    def claude_allowed_tools(self) -> str | None:
        return self._allowed_tools or getattr(self._cfg, 'claude_allowed_tools', None)

    @property
    def permission_mode(self) -> str | None:
        return self._permission_mode or getattr(self._cfg, 'permission_mode', None)

    @property
    def claude_add_dirs(self) -> str | None:
        # Ensure the session attachments directory is readable by Claude
        base = getattr(self._cfg, 'claude_add_dirs', None)
        try:
            from ...core.logging import get_core_logger
            core = get_core_logger()
            attach_dir = (core.core_log_dir / self._session_id / 'attachments').resolve()
            parts = [p.strip() for p in (base or '').split(',') if p.strip()]
            if str(attach_dir) not in parts:
                parts.append(str(attach_dir))
            return ','.join(parts) if parts else None
        except Exception:
            return base

    def _build_mcp_config_json(self):
        path = getattr(self._cfg, 'mcp_config_path', None)
        if path:
            p = Path(path)
            if not p.is_absolute():
                p = Path(self._cfg.project_root) / p
        else:
            p = Path(self._cfg.project_root) / '.claude' / 'settings.local.json'

        if not p.exists():
            return None

        try:
            with open(p, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return None

        servers = data.get('mcpServers') or {}
        if not isinstance(servers, dict) or not servers:
            return None

        perms = getattr(self._cfg, 'permissions', None)
        if perms and getattr(perms, 'mcp', None):
            allowed_servers = set(perms.mcp.keys())
            servers = {k: v for k, v in servers.items() if k in allowed_servers}

        return {'mcpServers': servers} if servers else None

    def _compute_allowed_tools(self) -> str | None:
        # Implementation would go here - simplified for now
        return None

    def _compute_permission_mode(self) -> str | None:
        # Implementation would go here - simplified for now
        return None


# Global components - will be initialized in main()
config = None
app = None
claude_backend = None
logger = None
processor = None
scheduler = None
bot_user_id = None  # Will be set during initialization

# User profile caching to avoid rate limiting
USER_NAME_CACHE: Dict[str, tuple] = {}
USER_TZ_CACHE: Dict[str, tuple] = {}
USER_CACHE_TTL = 3600  # 1 hour

async def _get_user_name(client, user_id: str) -> str:
    """Get user's display name from Slack API with caching, fallback to formatted user_id."""
    import time
    current_time = time.time()
    
    # Check cache first
    if user_id in USER_NAME_CACHE:
        cached_name, cached_time = USER_NAME_CACHE[user_id]
        if current_time - cached_time < USER_CACHE_TTL:
            return cached_name
    
    try:
        user_info = await client.users_info(user=user_id)
        user_data = user_info["user"]
        # Try display_name first, then real_name, then name, finally fallback to formatted user_id
        user_name = (user_data.get("profile", {}).get("display_name") or 
                     user_data.get("real_name") or 
                     user_data.get("name") or 
                     f"User_{user_id}")
        
        # Cache the result
        USER_NAME_CACHE[user_id] = (user_name, current_time)
        return user_name
        
    except Exception as e:
        print(f"Failed to get user name for {user_id}: {e}")
        # Return a more Claude-friendly format instead of raw user_id
        fallback_name = f"User_{user_id}"
        # Cache the fallback to avoid repeated API failures
        USER_NAME_CACHE[user_id] = (fallback_name, current_time)
        return fallback_name

async def _get_user_timezone(client, user_id: str) -> Optional[str]:
    """Get the user's IANA timezone (e.g., 'America/Chicago') using users_info, with caching."""
    import time
    now = time.time()
    # Check cache first
    if user_id in USER_TZ_CACHE:
        tz_value, cached_time = USER_TZ_CACHE[user_id]
        if now - cached_time < USER_CACHE_TTL:
            return tz_value
    try:
        info = await client.users_info(user=user_id)
        user = info.get("user", {})
        tz = user.get("tz")
        if not tz:
            offset = user.get("tz_offset")
            if isinstance(offset, int):
                hours = int(offset // 3600)
                minutes = int(abs(offset) % 3600 // 60)
                sign = "+" if hours >= 0 else "-"
                tz = f"UTC{sign}{abs(hours):02d}:{minutes:02d}"
        USER_TZ_CACHE[user_id] = (tz, now)
        return tz
    except Exception:
        USER_TZ_CACHE[user_id] = (None, now)
        return None

async def _get_thread_context(client, channel: str, thread_ts: str, bot_user_id: str) -> str:
    """
    Fetch thread messages since the bot's last response and format them for Claude.
    
    Args:
        client: Slack API client
        channel: Channel ID
        thread_ts: Thread timestamp
        bot_user_id: Bot's user ID to identify its previous responses
    
    Returns:
        Formatted thread context string
    """
    try:
        # Fetch thread replies
        response = await client.conversations_replies(
            channel=channel,
            ts=thread_ts,
            oldest=thread_ts  # Start from thread root
        )
        
        # Check if the API call was successful
        if not response.get("ok"):
            error_msg = response.get("error", "Unknown error")
            needed_scope = response.get("needed", "")
            provided_scopes = response.get("provided", "")
            print(f"Slack API error for thread context: {error_msg}")
            if needed_scope:
                print(f"Missing scope: {needed_scope}")
                print(f"Current scopes: {provided_scopes}")
                if needed_scope == "groups:history":
                    print("💡 This is a private channel. Add 'groups:history' and 'groups:read' scopes to your Slack app.")
                elif needed_scope == "channels:history":
                    print("💡 This is a public channel. Add 'channels:history' scope to your Slack app.")
            return ""
        
        messages = response.get("messages", [])
        if not messages:
            return ""
        
        # Find bot messages in the thread
        bot_message_indices = []
        for i, msg in enumerate(messages):
            user_id = msg.get("user")
            bot_id = msg.get("bot_id")
            if user_id == bot_user_id or bot_id:
                bot_message_indices.append(i)
        
        # Get messages since the bot's last response
        if bot_message_indices:
            # Use messages after the most recent bot message (last actual response)
            start_index = bot_message_indices[-1] + 1
            relevant_messages = messages[start_index:]
        else:
            # No bot messages found, include all messages
            relevant_messages = messages
        
        if not relevant_messages:
            return ""
        
        # Format messages for Claude
        context_parts = ["Thread context:"]
        for msg in relevant_messages:
            user_id = msg.get("user")
            text = msg.get("text", "")
            
            if user_id and text:
                user_name = await _get_user_name(client, user_id)
                context_parts.append(f"- {user_name}: {text}")
        
        result = "\n".join(context_parts) if len(context_parts) > 1 else ""
        return result
        
    except Exception as e:
        print(f"Failed to fetch thread context for {channel}/{thread_ts}: {e}")
        return ""

async def _check_bot_permissions():
    """Check if the bot has necessary permissions for thread context functionality."""
    try:
        # Try to get auth info which includes scopes
        auth_response = await app.client.auth_test()
        print(f"✅ Bot authentication successful")
        
        # The auth_test response doesn't include scopes, but we can test the permissions
        # by trying a simple API call that requires the scope we need
        print("🔍 Checking thread context permissions...")
        
    except Exception as e:
        print(f"⚠️  Permission check failed: {e}")

def initialize_components():
    """Initialize all components after configuration is loaded."""
    global config, app, claude_backend, logger, processor, scheduler, bot_user_id
    
    # Configuration is already loaded at module level
    validate_config()
    
    # Initialize Slack App
    app = AsyncApp(token=config.slack.bot_token)
    
    # Add global middleware for debugging (can be removed in production)
    @app.middleware
    async def log_all_events(req, resp, next):
        event_type = req.body.get('event', {}).get('type')
        print(f"DEBUG: Event type: {event_type}", flush=True)
        await next()
    
    # Initialize components with full bot config
    # Create ChatBackend with Slack config provider
    config_provider = _SlackConfigProvider(config)
    claude_backend = ChatBackend(config_provider)

    # Register Slack file upload handler
    slack_upload_handler = SlackUploadHandler(
        client=app.client,
        bot_token=config.slack.bot_token
    )
    claude_backend.register_upload_handler("slack", slack_upload_handler)

    logger = BotLogger(config.app)
    processor = MessageProcessor(claude_backend, logger, config)

    # Register event handlers
    register_event_handlers()

def register_event_handlers():
    """Register all event handlers with the app."""
    
    # Register message handler
    app.event("message")(handle_dm_message)
    # Register app mention handler  
    app.event("app_mention")(handle_app_mention)

async def handle_dm_message(event, say, ack, client):
    """Handle direct messages, channel mentions, and threaded messages with explicit mentions."""
    try:
        await ack()
        print(f"DEBUG: MESSAGE EVENT RECEIVED - channel_type: {event.get('channel_type')}, thread_ts: {event.get('thread_ts')}, text: {event.get('text', '')[:50]}", flush=True)
    except Exception as e:
        print(f"ERROR in handle_dm_message: {e}", flush=True)
        import traceback
        traceback.print_exc()
    
    # Skip bot messages to prevent loops
    if event.get("bot_id"):
        print(f"DEBUG: Skipping bot message")
        return
    
    channel_type = event.get("channel_type")
    user_id = event.get("user")
    channel = event.get("channel")
    text = event.get("text", "")
    files = event.get("files", []) or []
    
    # Get user name for Claude context
    user_name = await _get_user_name(client, user_id)
    
    
    # Handle command to send files before anything else
    if text.strip().lower().startswith('!sendfile'):
        try:
            # Parse paths after command
            parts = text.strip().split(maxsplit=1)
            if len(parts) < 2 or not parts[1].strip():
                # Reply in a thread (create one if needed)
                root_ts = event.get('thread_ts') or event.get('ts')
                await say("Usage: !sendfile <relative_or_absolute_path_under_session_attachments>", thread_ts=root_ts)
                return
            paths_arg = parts[1].strip()
            paths = [p for p in paths_arg.split() if p.strip()]
            session_id = f"slack_{channel}"
            results = []
            # Ensure follow-ups happen in a thread
            root_ts = event.get('thread_ts') or event.get('ts')
            for p in paths:
                try:
                    resp = await upload_local_file(
                        client,
                        channel=channel,
                        session_id=session_id,
                        file_path=p,
                        thread_ts=root_ts
                    )
                    ok = resp.get('ok', True)
                    if ok:
                        results.append(f"✅ {p}")
                    else:
                        results.append(f"❌ {p}: {resp.get('error','unknown error')}")
                except Exception as e:
                    results.append(f"❌ {p}: {e}")
            await say("\n".join(results), thread_ts=root_ts)
            return
        except Exception as e:
            root_ts = event.get('thread_ts') or event.get('ts')
            await _handle_error(e, user_id, channel, say, thread_ts=root_ts)
            return

    # Handle regular DMs first (regardless of thread status)
    if channel_type == "im":
        print(f"Processing DM from {user_name} ({user_id}) in {channel}: {text[:100]}{'...' if len(text) > 100 else ''}", flush=True)
        
        try:
            # Determine root thread ts and session id
            root_ts = event.get('thread_ts') or event.get('ts')
            session_id = f"slack_{channel}"

            # Real-time timezone handling (DM only): detect changes and refresh
            user_tz = await _get_user_timezone(client, user_id)
            try:
                prev_meta = processor.claude.get_session_metadata(session_id)
                prev_tz = prev_meta.get("user_timezone")
            except Exception:
                prev_tz = None

            if user_tz and prev_tz and user_tz != prev_tz:
                try:
                    await say(f"⏱️ Detected timezone change to `{user_tz}`. Refreshing context…", thread_ts=root_ts)
                except Exception:
                    pass
                try:
                    await logger.log_message(user_id, channel, f"Timezone change: {prev_tz} -> {user_tz}", "timezone_change")
                except Exception:
                    pass
                try:
                    processor.claude.update_session_metadata(session_id, user_timezone=user_tz)
                except Exception:
                    pass
                try:
                    await processor.claude.reset_session(session_id)
                except Exception:
                    pass
            else:
                # Ensure metadata is populated for first-time or consistent tz
                try:
                    if user_tz:
                        processor.claude.update_session_metadata(session_id, user_timezone=user_tz)
                except Exception:
                    pass

            # Download any attachments
            max_mb = getattr(config.app, 'attachments_max_size_mb', None) or 25
            max_bytes = int(max_mb) * 1024 * 1024
            allowed_types = None
            if getattr(config.app, 'attachments_allowed_types', None):
                allowed_types = [s.strip() for s in str(config.app.attachments_allowed_types).split(',') if s.strip()]

            saved, dl_errors = await download_all_from_event_files(
                files=files,
                session_id=session_id,
                bot_token=config.slack.bot_token,
                max_bytes=max_bytes,
                allowed_types=allowed_types,
            )
            attachment_paths = [str(x.path) for x in saved]
            if dl_errors:
                await logger.log_message(user_id, channel, f"Attachment errors: {dl_errors}", "attachment_errors")
            # Log incoming message
            await logger.log_message(user_id, channel, text, "incoming")

            # Process message with streaming support
            response, interim_ts = await processor.process_message(
                text, channel, user_name, user_id,
                user_timezone=user_tz,
                attachment_paths=attachment_paths,
                slack_client=client,
                thread_ts=root_ts
            )

            # Only post new message if interim message wasn't updated
            if not interim_ts:
                await say(response, thread_ts=root_ts)

            # Log outgoing message
            await logger.log_message(user_id, channel, response, "outgoing")
            
        except Exception as e:
            root_ts = event.get('thread_ts') or event.get('ts')
            await _handle_error(e, user_id, channel, say, thread_ts=root_ts)
        return
    
    # Handle threaded messages with explicit mentions (non-DM channels)
    if event.get("thread_ts") and _is_explicit_mention(text):
        # Check sendfile command in thread
        if text.strip().lower().startswith('!sendfile'):
            try:
                parts = text.strip().split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    await say("Usage: !sendfile <relative_or_absolute_path_under_session_attachments>", thread_ts=event.get('thread_ts'))
                    return
                paths_arg = parts[1].strip()
                paths = [p for p in paths_arg.split() if p.strip()]
                session_id = f"slack_{channel}"
                results = []
                for p in paths:
                    try:
                        resp = await upload_local_file(
                            client,
                            channel=channel,
                            session_id=session_id,
                            file_path=p,
                            thread_ts=event.get('thread_ts')
                        )
                        ok = resp.get('ok', True)
                        if ok:
                            results.append(f"✅ {p}")
                        else:
                            results.append(f"❌ {p}: {resp.get('error','unknown error')}")
                    except Exception as e:
                        results.append(f"❌ {p}: {e}")
                await say("\n".join(results), thread_ts=event.get('thread_ts'))
                return
            except Exception as e:
                await _handle_error(e, user_id, channel, say, thread_ts=event.get('thread_ts'))
                return
        files = event.get("files", []) or []
        cleaned_text = _clean_mention_text(text)
        thread_ts = event.get("thread_ts")
        print(f"Processing threaded mention from {user_id} in {channel}: {cleaned_text[:100]}{'...' if len(cleaned_text) > 100 else ''}", flush=True)
        
        try:
            # Download any attachments
            session_id = f"slack_{channel}"
            max_mb = getattr(config.app, 'attachments_max_size_mb', None) or 25
            max_bytes = int(max_mb) * 1024 * 1024
            allowed_types = None
            if getattr(config.app, 'attachments_allowed_types', None):
                allowed_types = [s.strip() for s in str(config.app.attachments_allowed_types).split(',') if s.strip()]
            saved, dl_errors = await download_all_from_event_files(
                files=files,
                session_id=session_id,
                bot_token=config.slack.bot_token,
                max_bytes=max_bytes,
                allowed_types=allowed_types,
            )
            attachment_paths = [str(x.path) for x in saved]
            # Get thread context if bot_user_id is available
            thread_context = ""
            if bot_user_id:
                thread_context = await _get_thread_context(client, channel, thread_ts, bot_user_id)
            
            # Use thread context if available, otherwise just the current message  
            if thread_context:
                # Thread context now includes the current mention, so use it directly
                full_message = thread_context
            else:
                # No thread context, use current message with user context
                full_message = f"User: {user_name}\nMessage: {cleaned_text}"
            
            # Log incoming message (log the original message, not the context)
            await logger.log_message(user_id, channel, cleaned_text, "incoming_thread")

            # Process message with streaming support
            response, interim_ts = await processor.process_message(
                full_message, channel, user_name, user_id,
                attachment_paths=attachment_paths,
                slack_client=client,
                thread_ts=thread_ts
            )

            # Only post new message if interim message wasn't updated
            if not interim_ts:
                await say(response, thread_ts=thread_ts)

            # Log outgoing message
            await logger.log_message(user_id, channel, response, "outgoing_thread")
            
        except Exception as e:
            await _handle_error(e, user_id, channel, say, thread_ts=thread_ts)
        return
    
    # Handle channel mentions (not in threads)
    if not event.get("thread_ts") and _is_explicit_mention(text):
        # Check sendfile command in channel mention
        if text.strip().lower().startswith('!sendfile'):
            try:
                parts = text.strip().split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    await say("Usage: !sendfile <relative_or_absolute_path_under_session_attachments>")
                    return
                paths_arg = parts[1].strip()
                paths = [p for p in paths_arg.split() if p.strip()]
                session_id = f"slack_{channel}"
                results = []
                for p in paths:
                    try:
                        resp = await upload_local_file(
                            client,
                            channel=channel,
                            session_id=session_id,
                            file_path=p,
                            thread_ts=event.get('ts')
                        )
                        ok = resp.get('ok', True)
                        if ok:
                            results.append(f"✅ {p}")
                        else:
                            results.append(f"❌ {p}: {resp.get('error','unknown error')}")
                    except Exception as e:
                        results.append(f"❌ {p}: {e}")
                await say("\n".join(results), thread_ts=event.get('ts'))
                return
            except Exception as e:
                await _handle_error(e, user_id, channel, say, thread_ts=event.get('ts'))
                return
        files = event.get("files", []) or []
        cleaned_text = _clean_mention_text(text)
        # Create a new thread from this message
        thread_ts = event.get("ts")
        print(f"Processing channel mention from {user_id} in {channel}: {cleaned_text[:100]}{'...' if len(cleaned_text) > 100 else ''}", flush=True)
        
        try:
            # Download any attachments
            session_id = f"slack_{channel}"
            max_mb = getattr(config.app, 'attachments_max_size_mb', None) or 25
            max_bytes = int(max_mb) * 1024 * 1024
            allowed_types = None
            if getattr(config.app, 'attachments_allowed_types', None):
                allowed_types = [s.strip() for s in str(config.app.attachments_allowed_types).split(',') if s.strip()]
            saved, dl_errors = await download_all_from_event_files(
                files=files,
                session_id=session_id,
                bot_token=config.slack.bot_token,
                max_bytes=max_bytes,
                allowed_types=allowed_types,
            )
            attachment_paths = [str(x.path) for x in saved]
            # Log incoming message
            await logger.log_message(user_id, channel, cleaned_text, "incoming_mention")

            # Process message with streaming support
            response, interim_ts = await processor.process_message(
                cleaned_text, channel, user_name, user_id,
                attachment_paths=attachment_paths,
                slack_client=client,
                thread_ts=thread_ts
            )

            # Only post new message if interim message wasn't updated
            if not interim_ts:
                await say(response, thread_ts=thread_ts)

            # Log outgoing message
            await logger.log_message(user_id, channel, response, "outgoing_mention")
            
        except Exception as e:
            await _handle_error(e, user_id, channel, say, thread_ts=thread_ts)
        return
    
    # Skip threaded messages that don't explicitly mention the bot
    if event.get("thread_ts") and not _is_explicit_mention(text):
        return
    
    # Skip channel messages that aren't mentions
    if not _is_explicit_mention(text):
        return

async def handle_app_mention(event, say, ack, client):
    """Handle app mentions in channels."""
    await ack()
    # DEBUG: Uncomment for detailed app_mention debugging  
    # print(f"DEBUG: APP_MENTION EVENT RECEIVED: {event}")
    
    user_id = event.get("user")
    channel = event.get("channel")
    original_text = event.get("text", "")
    
    # Get user name for Claude context
    user_name = await _get_user_name(client, user_id)
    
    # All app_mention events are explicit mentions by definition
    # No need to check _is_explicit_mention here
    
    # Remove bot mention from text
    text = _clean_mention_text(original_text)
    
    # Use thread_ts if message is already in a thread, otherwise create new thread
    thread_ts = event.get("thread_ts") or event.get("ts")
    
    try:
        print(f"Processing mention from {user_id} in {channel}: '{text}'", flush=True)
        
        # Get thread context if this is in a thread
        thread_context = ""
        if event.get("thread_ts") and bot_user_id:  # Reply in existing thread
            thread_context = await _get_thread_context(client, channel, event.get("thread_ts"), bot_user_id)
        
        # Use thread context if available, otherwise just the current message
        if thread_context:
            # Thread context now includes the current mention, so use it directly
            full_message = thread_context
        else:
            # No thread context, use current message with user context
            full_message = f"User: {user_name}\nMessage: {text}"
        
        # Log incoming message (log the original message, not the context)
        await logger.log_message(user_id, channel, text, "incoming_mention")

        # Process message with streaming support
        response, interim_ts = await processor.process_message(
            full_message, channel, user_name, user_id,
            slack_client=client,
            thread_ts=thread_ts
        )

        # Only post new message if interim message wasn't updated
        if not interim_ts:
            await say(response, thread_ts=thread_ts)

        # Log outgoing message
        await logger.log_message(user_id, channel, response, "outgoing_mention")
        
    except Exception as e:
        await _handle_error(e, user_id, channel, say, thread_ts=thread_ts)

#%% Helper Functions

def _is_explicit_mention(text: str) -> bool:
    """
    Check if the message explicitly mentions the bot.
    
    Args:
        text: Message text to check
        
    Returns:
        True if the message contains an explicit bot mention
    """
    # Check for bot mentions in the format <@U123456789>
    return bool(re.search(r'<@U[A-Z0-9]+>', text))

def _clean_mention_text(text: str) -> str:
    """
    Remove bot mention from message text.
    
    Args:
        text: Original message text with bot mention
        
    Returns:
        Cleaned text without bot mention
    """
    # Remove bot mentions in the format <@U123456789>
    cleaned = re.sub(r'<@U[A-Z0-9]+>', '', text).strip()
    return cleaned

async def _handle_error(error: Exception, user_id: str, channel: str, say, thread_ts: Optional[str] = None):
    """
    Handle errors that occur during message processing.
    
    Args:
        error: The exception that occurred
        user_id: Slack user ID
        channel: Slack channel ID
        say: Slack say function
        thread_ts: Optional thread timestamp for threaded responses
    """
    print(f"Error processing message from {user_id} in {channel}: {error}")
    
    # Log error using current logger instance
    try:
        if logger:  # Make sure logger exists
            await logger.log_error(user_id, channel, str(error))
        else:
            print(f"Logger not initialized, cannot log error: {error}")
    except Exception as log_error:
        print(f"Failed to log error: {log_error}")
    
    # Send user-friendly error message
    if isinstance(error, BotError):
        error_msg = f"🚨 {str(error)}"
    else:
        error_msg = "🚨 Sorry, I encountered an unexpected error. Please try again or contact support."
    
    try:
        if thread_ts:
            await say(error_msg, thread_ts=thread_ts)
        else:
            await say(error_msg)
    except Exception as say_error:
        print(f"Failed to send error message: {say_error}")

async def _cleanup_task():
    """Periodic cleanup task for old sessions."""
    while True:
        try:
            # Sleep for 1 hour
            await asyncio.sleep(3600)
            
            # Clean up old sessions
            cleaned = await processor.cleanup_old_sessions(max_age_hours=24)
            if cleaned > 0:
                print(f"Cleaned up {cleaned} old conversation sessions")
                
        except Exception as e:
            print(f"Error in cleanup task: {e}")

async def _start_cleanup_task():
    """Start the cleanup task and scheduler in the background."""
    asyncio.create_task(_cleanup_task())
    
    # Start the scheduler if there are scheduled messages
    if scheduler and scheduler.scheduled_messages:
        await scheduler.start()
        print(f"📅 Started scheduler with {len(scheduler.scheduled_messages)} scheduled messages")
    else:
        print("📅 No scheduled messages configured, scheduler not started")

#%% Main entry point

async def main():
    """Main entry point for the Slack Claude Bot."""
    try:
        # Force unbuffered output
        import sys
        sys.stdout.reconfigure(line_buffering=True)
        print("🚀 Starting Slack Claude Bot...", flush=True)
        
        # Initialize all components
        initialize_components()
        
        # Get bot user ID for thread context functionality
        global bot_user_id
        try:
            auth_response = await app.client.auth_test()
            bot_user_id = auth_response["user_id"]
            print(f"✅ Bot user ID: {bot_user_id}")
            
            # Check bot permissions for thread context functionality
            await _check_bot_permissions()
            
        except Exception as e:
            print(f"⚠️  Warning: Could not get bot user ID: {e}")
            bot_user_id = None
        
        # Initialize scheduler with app instance
        global scheduler
        scheduler = AsyncScheduler(config, processor, logger, app)
        scheduler.load_scheduled_messages(config.scheduled_messages)
        
        # Print configuration status
        print_config_status()
        
        # Start background cleanup task and scheduler
        await _start_cleanup_task()
        
        print("✅ Bot is ready to receive messages!")
        print("📱 Available in:")
        print("   • Direct messages")
        print("   • Explicit channel mentions (@bot_name)")
        print("   • Threaded messages with explicit mentions")
        print("   • Type !help for available commands")
        print("-" * 50)
        
        # Start the Socket Mode handler
        handler = AsyncSocketModeHandler(app, config.slack.app_token)
        await handler.start_async()
        
    except ConfigurationError as e:
        print(f"❌ Configuration error: {e}")
        print("Please check your .env file and ensure all required variables are set.")
        return 1
    except Exception as e:
        print(f"❌ Failed to start bot: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    
    # Use project path from argument or current working directory
    if len(sys.argv) > 1:
        project_path = sys.argv[1]
        print(f"Using project path from argument: {project_path}")
    else:
        project_path = os.getcwd()
        print(f"Using current working directory: {project_path}")
    
    # Load configuration from the project directory
    config = load_config(project_path)
    
    exit_code = asyncio.run(main())
    exit(exit_code or 0)
