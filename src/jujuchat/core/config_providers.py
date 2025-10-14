from __future__ import annotations

import yaml
from typing import Optional, Dict, Any
from pathlib import Path
from dataclasses import dataclass

from .config import SessionConfig, ConfigProvider


@dataclass
class IOSSessionConfig:
    """iOS-specific session configuration implementing SessionConfig protocol."""
    
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
    
    # MCP + permissions extensions
    mcp_config_path: Optional[str] = None
    enabled_mcp_servers: Optional[str] = None
    obsidian_allowed_projects: Optional[str] = None
    permissions: Optional[object] = None

    # History and attachments
    history_dir: Path | None = None
    attachments_max_size_mb: Optional[int] = None
    attachments_allowed_types: Optional[str] = None


class IOSConfigProvider:
    """Configuration provider for iOS HTTP sessions."""
    
    def __init__(self, config_path: Path):
        """Initialize with path to iOS configuration YAML."""
        self.config_path = config_path
        self._config_data: Optional[Dict[str, Any]] = None
        self._load_config()
    
    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                self._config_data = yaml.safe_load(f)
            # Remember config directory for file interpolation
            self._config_dir = self.config_path.parent.resolve()
        except Exception as e:
            raise RuntimeError(f"Failed to load iOS config from {self.config_path}: {e}")

    def _interpolate_string(self, text: str) -> str:
        """Support ${file:path} interpolation in string values (e.g., system_prompt)."""
        import re
        def replace_file(m):
            rel = m.group(1).strip()
            try:
                p = (self._config_dir / rel).expanduser().resolve()
                if not p.exists() or not p.is_file():
                    raise FileNotFoundError(rel)
                if p.stat().st_size > 1024 * 1024:
                    raise ValueError(f"File too large: {rel}")
                return p.read_text(encoding='utf-8').rstrip()
            except Exception as e:
                return f"<error reading {rel}: {e}>"
        return re.sub(r"\$\{file:([^}]+)\}", replace_file, text or "")
    
    def get_session_config(self, session_id: str) -> SessionConfig:
        """Return session configuration for iOS clients."""
        if not self._config_data:
            raise RuntimeError("Configuration not loaded")
        
        config = self._config_data
        
        # Build paths
        project_root = Path(config.get('project_root', '.')).expanduser().resolve()
        log_dir = Path(config.get('log_dir', project_root / 'logs')).expanduser().resolve()

        # Expand user paths for optional fields that are used as raw strings
        def _expand_path_str(val: Optional[str]) -> Optional[str]:
            if not val:
                return None
            try:
                return str(Path(val).expanduser().resolve())
            except Exception:
                # If resolution fails, still expand '~' for robustness
                try:
                    return str(Path(val).expanduser())
                except Exception:
                    return val

        # Expand claude_initial_path and mcp_config_path
        expanded_initial_path = _expand_path_str(config.get('claude_initial_path'))
        expanded_mcp_config_path = _expand_path_str(config.get('mcp_config_path'))

        # Expand any comma-separated directories in claude_add_dirs
        raw_add_dirs = config.get('claude_add_dirs')
        expanded_add_dirs: Optional[str] = None
        if isinstance(raw_add_dirs, str) and raw_add_dirs.strip():
            parts = [p.strip() for p in raw_add_dirs.split(',') if p.strip()]
            expanded_parts = [_expand_path_str(p) or p for p in parts]
            expanded_add_dirs = ','.join(expanded_parts) if expanded_parts else None
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # History directory (default under log_dir)
        history_dir = Path(config.get('history_dir', log_dir / 'history')).expanduser().resolve()
        history_dir.mkdir(parents=True, exist_ok=True)

        # Ensure history_dir is in add-dirs so Claude can access saved files
        add_dirs_list = []
        if expanded_add_dirs:
            add_dirs_list = [p.strip() for p in expanded_add_dirs.split(',') if p.strip()]
        if str(history_dir) not in add_dirs_list:
            add_dirs_list.append(str(history_dir))
        expanded_add_dirs = ','.join(add_dirs_list) if add_dirs_list else None

        # Attachments settings
        attachments_cfg = config.get('attachments') or {}
        attachments_max_size_mb = attachments_cfg.get('max_size_mb', 25)
        # Stored as comma-separated to align with other string fields
        allowed_types = attachments_cfg.get('allowed_types')
        if isinstance(allowed_types, list):
            attachments_allowed_types = ','.join([str(t).strip() for t in allowed_types if str(t).strip()])
        else:
            attachments_allowed_types = str(allowed_types).strip() if allowed_types else None

        # Extract permissions if present
        permissions = config.get('permissions')
        
        # Interpolate system_prompt if it references file content
        sys_prompt_raw = config.get('system_prompt')
        sys_prompt = self._interpolate_string(sys_prompt_raw) if isinstance(sys_prompt_raw, str) else sys_prompt_raw

        return IOSSessionConfig(
            # Paths and commands
            project_root=project_root,
            log_dir=log_dir,
            claude_command=config.get('claude_command', 'claude'),
            
            # Claude settings
            max_response_length=config.get('max_response_length', 8000),
            system_prompt=sys_prompt,
            claude_model=config.get('claude_model'),
            claude_max_turns=config.get('claude_max_turns'),
            claude_verbose=config.get('claude_verbose', False),
            claude_allowed_tools=config.get('claude_allowed_tools'),
            claude_disallowed_tools=config.get('claude_disallowed_tools'),
            claude_add_dirs=expanded_add_dirs,
            claude_initial_path=expanded_initial_path,
            permission_mode=config.get('permission_mode'),
            
            # MCP + permissions extensions
            mcp_config_path=expanded_mcp_config_path,
            enabled_mcp_servers=config.get('enabled_mcp_servers'),
            obsidian_allowed_projects=config.get('obsidian_allowed_projects'),
            permissions=permissions,

            # History and attachments
            history_dir=history_dir,
            attachments_max_size_mb=attachments_max_size_mb,
            attachments_allowed_types=attachments_allowed_types,
        )
