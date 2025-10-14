from __future__ import annotations

from typing import Protocol, runtime_checkable, Optional
from pathlib import Path


@runtime_checkable
class SessionConfig(Protocol):
    """Protocol describing the attributes required by the backend for a session.

    This intentionally mirrors the merged AppConfig returned by the Slack bot's
    BotConfig.get_channel_config(...) to avoid duplication while enabling reuse.
    """

    # Paths and commands
    project_root: Path
    log_dir: Path
    claude_command: str

    # Claude settings
    max_response_length: int
    system_prompt: Optional[str]
    claude_model: Optional[str]
    claude_max_turns: Optional[int]
    claude_verbose: bool
    claude_allowed_tools: Optional[str]
    claude_disallowed_tools: Optional[str]
    claude_add_dirs: Optional[str]
    claude_initial_path: Optional[str]
    permission_mode: Optional[str]
    # History and attachments
    history_dir: Path
    attachments_max_size_mb: Optional[int]
    attachments_allowed_types: Optional[str]
    # Optional MCP + permissions extensions (duck-typed; frontends may omit)
    mcp_config_path: Optional[str]
    enabled_mcp_servers: Optional[str]
    # Note: design is whitelist-only; no disabled list
    obsidian_allowed_projects: Optional[str]
    # Permissions object or mapping: expects keys/attrs tools, mcp, mode
    permissions: Optional[object]


@runtime_checkable
class ConfigProvider(Protocol):
    """Frontend adapter that provides a per-session configuration.

    Implementations adapt existing app configs (Slack, iOS/HTTP, CLI) to the
    backend by returning a session-specific config object that satisfies
    SessionConfig.
    """

    def get_session_config(self, session_id: str) -> SessionConfig:  # pragma: no cover - protocol
        ...
