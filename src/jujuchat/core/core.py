from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    Message,
    ResultMessage,
    SystemMessage,
    TextBlock,
)

from .config import ConfigProvider


logger = logging.getLogger(__name__)


class ClaudeError(Exception):
    """Exception raised for Claude backend errors."""


DEFAULT_SYSTEM_PROMPT = (
    "You are a programming assistant helping with codebase analysis and development.\n\n"
    "CAPABILITIES:\n"
    "- You can read files using: Read, Grep, Glob, LS, WebSearch\n"
    "- You can create and edit files using: Write, Edit, MultiEdit\n"
    "- You can use git commands for reading: git log, git status, git diff, git show\n"
    "- You can run bash commands and execute code\n"
    "- Be concise and research-focused in your responses\n"
    "- When analyzing Julia code, provide Julia-specific insights and idiomatic recommendations"
)

STREAM_RECEIVE_TIMEOUT = 180.0  # seconds


StreamHandler = Callable[[Dict[str, Any]], Awaitable[None]]


@dataclass
class SessionState:
    """Holds per-session Claude SDK client and related metadata."""

    client: ClaudeSDKClient
    config_signature: str
    sdk_session_id: Optional[str] = None


class ChatBackend:
    """Persistent, frontend-agnostic interface to Claude Code via the Agent SDK."""

    def __init__(self, config_provider: ConfigProvider):
        self.config_provider = config_provider
        self._global_lock = asyncio.Lock()
        self._sessions: Dict[str, SessionState] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    async def send_message_with_session(
        self,
        message: str,
        session_id: str,
        *,
        stream_handler: Optional[StreamHandler] = None,
    ) -> str:
        """Send a message to a persistent session and return assistant text."""
        if session_id not in self._locks:
            async with self._global_lock:
                if session_id not in self._locks:
                    self._locks[session_id] = asyncio.Lock()

        async with self._locks[session_id]:
            cfg = self.config_provider.get_session_config(session_id)
            state = await self._get_or_create_session(session_id, cfg)

            try:
                await state.client.query(message)
            except ClaudeSDKError as exc:
                await self._teardown_session(session_id, state)
                raise ClaudeError(f"Failed to send message to Claude: {exc}") from exc

            assistant_parts: List[str] = []
            raw_events: List[Dict[str, Any]] = []
            result_message: Optional[ResultMessage] = None

            try:
                # Use async for - let it complete naturally without break
                response_iterator = state.client.receive_response().__aiter__()

                while True:
                    try:
                        # Timeout per message to prevent hanging
                        sdk_message = await asyncio.wait_for(
                            response_iterator.__anext__(),
                            timeout=STREAM_RECEIVE_TIMEOUT,
                        )
                    except StopAsyncIteration:
                        # Iterator completed normally
                        break
                    except asyncio.TimeoutError as exc:
                        raise ClaudeError(
                            f"Timed out waiting for Claude response after {STREAM_RECEIVE_TIMEOUT} seconds"
                        ) from exc

                    event = self._message_to_event(sdk_message)
                    raw_events.append(event)

                    if stream_handler is not None:
                        try:
                            await stream_handler(event)
                        except Exception as exc:  # pragma: no cover - logging branch only
                            if self._should_log_stream_errors(cfg):
                                logger.warning(
                                    "Stream handler for session %s raised an exception: %s",
                                    session_id,
                                    exc,
                                    exc_info=True,
                                )

                    if isinstance(sdk_message, SystemMessage):
                        state.sdk_session_id = sdk_message.data.get("session_id", state.sdk_session_id)
                    elif isinstance(sdk_message, AssistantMessage):
                        text_chunk = self._extract_text(sdk_message)
                        if text_chunk:
                            assistant_parts.append(text_chunk)
                    elif isinstance(sdk_message, ResultMessage):
                        result_message = sdk_message
                        if sdk_message.session_id:
                            state.sdk_session_id = sdk_message.session_id
                        # Don't break - let iteration complete naturally
            except ClaudeSDKError as exc:
                await self._teardown_session(session_id, state)
                raise ClaudeError(f"Error receiving response from Claude: {exc}") from exc

            if not assistant_parts and result_message and result_message.result:
                assistant_parts.append(result_message.result)

            if not assistant_parts:
                raise ClaudeError("No assistant message received from Claude Agent SDK")

            response_text = self._clean_response(
                "\n".join(part for part in assistant_parts if part),
                cfg.max_response_length,
            )

            if not response_text:
                raise ClaudeError("Received empty response from Claude Agent SDK")

            await self._log_raw_json(session_id, message, raw_events)
            return response_text

    async def interrupt_session(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if not state:
            return
        try:
            await state.client.interrupt()
        except ClaudeSDKError as exc:
            raise ClaudeError(f"Failed to interrupt session {session_id}: {exc}") from exc

    async def reset_session(self, session_id: str) -> None:
        state = self._sessions.pop(session_id, None)
        if not state:
            return
        try:
            await state.client.disconnect()
        except Exception:
            pass
        if session_id in self._locks:
            del self._locks[session_id]
        logger.info("Reset Claude session %s", session_id)

    async def compact_session(self, session_id: str) -> bool:
        """Compact the conversation history for a session using Claude Code's /compact command.

        Returns:
            True if compact was successful, False if not supported or failed.
        """
        state = self._sessions.get(session_id)
        if not state:
            return False

        try:
            # Send the /compact command to Claude Code
            await state.client.query("/compact")

            # Consume the response and let iterator complete naturally
            response_iterator = state.client.receive_response().__aiter__()
            success = False

            while True:
                try:
                    sdk_message = await asyncio.wait_for(
                        response_iterator.__anext__(),
                        timeout=STREAM_RECEIVE_TIMEOUT,
                    )
                    # Check for success indication in the response
                    if isinstance(sdk_message, AssistantMessage):
                        text = self._extract_text(sdk_message)
                        if "compact" in text.lower() and ("success" in text.lower() or "completed" in text.lower()):
                            success = True
                except StopAsyncIteration:
                    # Iterator completed normally
                    break
                except asyncio.TimeoutError:
                    logger.warning("Timeout while compacting session %s", session_id)
                    return False

            if success:
                logger.info("Compacted session %s", session_id)
            return True  # Assume success if no errors
        except ClaudeSDKError as exc:
            logger.warning("Failed to compact session %s: %s", session_id, exc)
            return False

    async def cleanup_all_sessions(self) -> None:
        for sid in list(self._sessions.keys()):
            await self.reset_session(sid)

    def get_active_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    async def _get_or_create_session(self, session_id: str, cfg) -> SessionState:
        signature = self._config_signature(cfg)
        existing = self._sessions.get(session_id)
        if existing and existing.config_signature == signature:
            return existing

        if existing:
            await self._teardown_session(session_id, existing)

        return await self._create_session(session_id, cfg, signature)

    async def _create_session(self, session_id: str, cfg, signature: str) -> SessionState:
        options = self._build_agent_options(cfg)
        client = ClaudeSDKClient(options=options)

        try:
            await client.connect()
        except ClaudeSDKError as exc:
            raise ClaudeError(f"Failed to start Claude Agent SDK session: {exc}") from exc

        logger.info("Started Claude session %s", session_id)
        state = SessionState(client=client, config_signature=signature)
        self._sessions[session_id] = state
        return state

    async def _teardown_session(self, session_id: str, state: SessionState) -> None:
        try:
            await state.client.disconnect()
        except Exception:
            pass
        self._sessions.pop(session_id, None)
        logger.info("Tore down Claude session %s", session_id)

    def _build_agent_options(self, cfg) -> ClaudeAgentOptions:
        options = ClaudeAgentOptions()

        allowed_tools = self._compute_allowed_tools(cfg)
        if allowed_tools:
            options.allowed_tools = allowed_tools

        disallowed_tools = self._parse_csv(getattr(cfg, "claude_disallowed_tools", None))
        if disallowed_tools:
            options.disallowed_tools = disallowed_tools

        add_dirs = self._parse_csv(getattr(cfg, "claude_add_dirs", None))
        if add_dirs:
            options.add_dirs = add_dirs

        working_dir = cfg.claude_initial_path or str(cfg.project_root)
        if not Path(working_dir).exists():
            raise ClaudeError(f"Working directory '{working_dir}' does not exist")
        options.cwd = str(working_dir)

        options.system_prompt = cfg.system_prompt or DEFAULT_SYSTEM_PROMPT

        perm_mode = self._compute_permission_mode(cfg)
        if perm_mode:
            options.permission_mode = perm_mode

        if cfg.claude_model:
            options.model = cfg.claude_model
        if cfg.claude_max_turns is not None:
            options.max_turns = cfg.claude_max_turns

        mcp_servers = self._build_mcp_servers(cfg)
        if mcp_servers:
            options.mcp_servers = mcp_servers

        options.env = self._build_process_env(cfg)
        options.include_partial_messages = True
        options.setting_sources = ["project", "local", "user"]

        return options

    def _build_process_env(self, cfg) -> Dict[str, str]:
        env = os.environ.copy()

        projects = getattr(cfg, "obsidian_allowed_projects", None)
        if projects:
            env["MCP_ALLOWED_PROJECTS"] = projects

        try:
            mcp_env = self._collect_mcp_env(cfg)
            if mcp_env:
                env.update(mcp_env)
        except Exception:
            pass

        path_val = env.get("PATH", "")
        path_parts = path_val.split(":") if path_val else []

        claude_cmd = str(getattr(cfg, "claude_command", "")).strip()
        if claude_cmd and "/" in claude_cmd:
            cmd_path = Path(claude_cmd).expanduser()
            cmd_dir = str(cmd_path.parent.resolve())
            if cmd_dir not in path_parts:
                path_parts.insert(0, cmd_dir)
            env.setdefault("CLAUDE_CODE_EXECUTABLE", str(cmd_path.resolve()))

        node_exe = shutil.which("node")
        if node_exe:
            node_dir = str(Path(node_exe).parent)
            if node_dir not in path_parts:
                path_parts.insert(0, node_dir)

        brew_bin = "/opt/homebrew/bin"
        if brew_bin not in path_parts and Path(brew_bin).exists():
            path_parts.append(brew_bin)

        env["PATH"] = ":".join(path_parts) if path_parts else path_val
        return env

    def _build_mcp_servers(self, cfg) -> Dict[str, Any]:
        servers = self._load_mcp_servers(cfg)
        if not servers:
            return {}
        filtered = self._filter_mcp_servers(servers, cfg)
        return filtered

    def _parse_csv(self, value: Optional[str]) -> List[str]:
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    def _get_permissions(self, cfg) -> Optional[Dict[str, Any]]:
        perms = getattr(cfg, "permissions", None)
        if perms is None:
            return None
        if isinstance(perms, dict):
            return perms
        out: Dict[str, Any] = {}
        for key in ("tools", "mcp", "mode"):
            out[key] = getattr(perms, key, None)
        return out

    def _compute_allowed_tools(self, cfg) -> List[str]:
        perms = self._get_permissions(cfg)
        allowed: List[str] = []

        if perms:
            tools = perms.get("tools") or []
            if isinstance(tools, list):
                allowed.extend([t for t in tools if isinstance(t, str) and t.strip()])

            mcp_map = perms.get("mcp") or {}
            if isinstance(mcp_map, dict):
                for server, tool_list in mcp_map.items():
                    for tool in tool_list or []:
                        if isinstance(tool, str) and tool.strip():
                            allowed.append(f"mcp__{server}__{tool}")

        additional = self._parse_csv(getattr(cfg, "claude_allowed_tools", None))
        if additional:
            allowed.extend(additional)

        seen = set()
        unique: List[str] = []
        for tool in allowed:
            if tool not in seen:
                seen.add(tool)
                unique.append(tool)
        return unique

    def _compute_permission_mode(self, cfg) -> Optional[str]:
        perms = self._get_permissions(cfg)
        mode_val = getattr(cfg, "permission_mode", None)
        if perms and perms.get("mode"):
            mode_val = perms["mode"]

        if not mode_val:
            return None

        aliases = {
            "ask": "default",
            "allow": "bypassPermissions",
            "deny": "plan",
        }
        mode_str = str(mode_val).strip()
        normalized = aliases.get(mode_str.lower(), mode_str)
        valid = {"default", "plan", "acceptEdits", "bypassPermissions"}
        return normalized if normalized in valid else None

    def _load_mcp_servers(self, cfg) -> Optional[Dict[str, Any]]:
        path_hint = getattr(cfg, "mcp_config_path", None)
        base = Path(cfg.project_root)
        if path_hint:
            path = Path(path_hint)
            if not path.is_absolute():
                path = base / path
            if path.is_dir():
                path = path / ".claude" / "settings.local.json"
        else:
            path = base / ".claude" / "settings.local.json"

        try:
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            servers = data.get("mcpServers")
            return servers if isinstance(servers, dict) else None
        except Exception:
            return None

    def _filter_mcp_servers(self, servers: Dict[str, Any], cfg) -> Dict[str, Any]:
        perms = self._get_permissions(cfg)
        if not (perms and isinstance(perms.get("mcp"), dict) and perms["mcp"]):
            return {}

        normalized = {str(name).lower(): (name, entry) for name, entry in servers.items()}
        allowed_names = {str(name).lower() for name in perms["mcp"].keys()}
        filtered_pairs = [normalized[name] for name in allowed_names if name in normalized]
        return {orig: entry for (orig, entry) in filtered_pairs}

    def _collect_mcp_env(self, cfg) -> Dict[str, str]:
        envs: Dict[str, str] = {}
        servers = self._load_mcp_servers(cfg)
        if not servers:
            return envs
        filtered = self._filter_mcp_servers(servers, cfg)
        for _, entry in filtered.items():
            if isinstance(entry, dict):
                env = entry.get("env")
                if isinstance(env, dict):
                    for key, value in env.items():
                        if (
                            isinstance(key, str)
                            and isinstance(value, str)
                            and key
                            and value
                        ):
                            envs[key] = value
        projects = getattr(cfg, "obsidian_allowed_projects", None)
        if projects:
            envs.setdefault("MCP_ALLOWED_PROJECTS", projects)
        return envs

    def _message_to_event(self, message: Message) -> Dict[str, Any]:
        event: Dict[str, Any] = {"type": message.__class__.__name__}

        if isinstance(message, AssistantMessage):
            event["text"] = self._extract_text(message)
            event["content"] = [
                self._normalize_payload(block) for block in message.content
            ]
        elif isinstance(message, ResultMessage):
            event.update(
                {
                    "subtype": message.subtype,
                    "usage": self._normalize_payload(message.usage),
                    "total_cost_usd": message.total_cost_usd,
                    "session_id": message.session_id,
                    "result": message.result,
                }
            )
        elif isinstance(message, SystemMessage):
            event.update(
                {
                    "subtype": message.subtype,
                    "data": self._normalize_payload(message.data),
                }
            )

        if is_dataclass(message):
            event["raw"] = self._normalize_payload(asdict(message))
        else:
            payload = getattr(message, "__dict__", None)
            if payload:
                event["raw"] = self._normalize_payload(payload)

        return event

    def _extract_text(self, message: AssistantMessage) -> str:
        parts: List[str] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                parts.append(block.text)
        return "".join(parts).strip()

    def _should_log_stream_errors(self, cfg) -> bool:
        return bool(getattr(cfg, "log_stream_errors", False))

    def _normalize_payload(self, payload: Any) -> Any:
        if is_dataclass(payload):
            return self._normalize_payload(asdict(payload))
        if isinstance(payload, dict):
            return {k: self._normalize_payload(v) for k, v in payload.items()}
        if isinstance(payload, list):
            return [self._normalize_payload(item) for item in payload]
        if isinstance(payload, Path):
            return str(payload)
        if isinstance(payload, (str, int, float, bool)) or payload is None:
            return payload
        return repr(payload)

    def _clean_response(self, response: str, max_len: int) -> str:
        cleaned = re.sub(r"\[[0-9;]*m", "", response)
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len] + "\n\n... (response truncated)"
        return cleaned.strip()

    async def _log_raw_json(
        self,
        session_id: str,
        user_message: str,
        events: List[Dict[str, Any]],
    ) -> None:
        try:
            import aiofiles
            from datetime import datetime

            cfg = self.config_provider.get_session_config(session_id)
            log_dir = cfg.log_dir
            log_dir.mkdir(parents=True, exist_ok=True)

            today = datetime.now().strftime("%Y-%m-%d")
            log_file = log_dir / f"claude_raw_{today}.jsonl"

            now = datetime.now().isoformat()
            records = [
                {
                    "timestamp": now,
                    "session": session_id,
                    "direction": "request",
                    "message": user_message,
                }
            ]

            for event in events:
                records.append(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "session": session_id,
                        "direction": "response",
                        "event": event,
                    }
                )

            async with aiofiles.open(log_file, "a", encoding="utf-8") as f:
                for record in records:
                    await f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _config_signature(self, cfg) -> str:
        relevant = {
            "model": getattr(cfg, "claude_model", None),
            "claude_command": getattr(cfg, "claude_command", None),
            "max_turns": getattr(cfg, "claude_max_turns", None),
            "system_prompt": getattr(cfg, "system_prompt", None),
            "allowed_tools": self._compute_allowed_tools(cfg),
            "disallowed_tools": self._parse_csv(getattr(cfg, "claude_disallowed_tools", None)),
            "add_dirs": self._parse_csv(getattr(cfg, "claude_add_dirs", None)),
            "permission_mode": self._compute_permission_mode(cfg),
            "cwd": getattr(cfg, "claude_initial_path", None) or str(cfg.project_root),
            "mcp_config_path": getattr(cfg, "mcp_config_path", None),
        }
        return json.dumps(relevant, sort_keys=True, default=str)
