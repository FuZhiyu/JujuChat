#%% Configuration Management
"""
Centralized YAML configuration management for the Slack Claude Bot.

This module handles all configuration settings from YAML files,
providing a single source of truth for application configuration.
YAML format provides excellent readability and maintainability with
support for anchors, references, and multi-line strings.
"""

import os
import yaml
import re
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path

@dataclass 
class SlackConfig:
    """Slack API configuration settings."""
    bot_token: str
    app_token: str

@dataclass
class Permissions:
    """Whitelist-only permission configuration."""
    tools: Optional[list[str]] = None  # Whitelisted Claude tools (Read, Grep, LS, etc.)
    mcp: Optional[Dict[str, list[str]]] = None  # MCP server -> allowed tools mapping
    mode: Optional[str] = "default"  # Permission mode: default, acceptEdits, plan, bypassPermissions
    
    def merge_with(self, other: Optional['Permissions']) -> 'Permissions':
        """Merge this permissions config with another, with this taking priority."""
        if other is None:
            return self
        if self is None:
            return other
            
        return Permissions(
            tools=self.tools or other.tools,
            mcp=self.mcp or other.mcp,
            mode=self.mode or other.mode
        )

@dataclass
class ChannelConfig:
    """Channel-specific configuration settings."""
    system_prompt: Optional[str] = None
    claude_model: Optional[str] = None
    claude_max_turns: Optional[int] = None  # Limit agentic turns (--max-turns CLI flag)
    claude_verbose: Optional[bool] = None
    claude_add_dirs: Optional[str] = None  # Comma-separated list of directories to add
    claude_initial_path: Optional[str] = None  # Initial working directory for Claude
    mcp_config_path: Optional[str] = None  # Path to MCP config JSON file to load
    max_response_length: Optional[int] = None
    
    # New whitelist-only permission system
    permissions: Optional[Permissions] = None
    
    # Deprecated options (kept for backward compatibility)
    claude_allowed_tools: Optional[str] = None
    claude_disallowed_tools: Optional[str] = None
    permission_mode: Optional[str] = None
    enabled_mcp_servers: Optional[str] = None
    disabled_mcp_servers: Optional[str] = None
    # Obsidian-System specific controls
    obsidian_allowed_projects: Optional[str] = None  # e.g., "Personal,TreasuryGIV"
    
    def merge_with_global(self, global_config: 'AppConfig') -> 'AppConfig':
        """Create a merged config with channel overrides applied to global settings."""
        # Merge permissions properly
        merged_permissions = None
        if self.permissions or global_config.permissions:
            if self.permissions:
                merged_permissions = self.permissions.merge_with(global_config.permissions)
            else:
                merged_permissions = global_config.permissions
        
        return AppConfig(
            project_root=global_config.project_root,
            log_dir=global_config.log_dir,
            claude_command=global_config.claude_command,
            max_response_length=self.max_response_length or global_config.max_response_length,
            system_prompt=self.system_prompt or global_config.system_prompt,
            claude_model=self.claude_model or global_config.claude_model,
            claude_max_turns=self.claude_max_turns if self.claude_max_turns is not None else global_config.claude_max_turns,
            claude_verbose=self.claude_verbose if self.claude_verbose is not None else global_config.claude_verbose,
            claude_add_dirs=self.claude_add_dirs or global_config.claude_add_dirs,
            claude_initial_path=self.claude_initial_path or global_config.claude_initial_path,
            mcp_config_path=self.mcp_config_path or global_config.mcp_config_path,
            permissions=merged_permissions,
            # Deprecated options (backward compatibility)
            claude_allowed_tools=self.claude_allowed_tools or global_config.claude_allowed_tools,
            claude_disallowed_tools=self.claude_disallowed_tools or global_config.claude_disallowed_tools,
            permission_mode=self.permission_mode or global_config.permission_mode,
            enabled_mcp_servers=self.enabled_mcp_servers or global_config.enabled_mcp_servers,
            disabled_mcp_servers=self.disabled_mcp_servers or global_config.disabled_mcp_servers,
            obsidian_allowed_projects=self.obsidian_allowed_projects or global_config.obsidian_allowed_projects
        )

@dataclass
class AppConfig:
    """Application-wide configuration settings."""
    project_root: Path
    log_dir: Path
    claude_command: str
    max_response_length: int
    system_prompt: Optional[str]
    claude_model: Optional[str]
    claude_max_turns: Optional[int]  # Limit agentic turns (--max-turns CLI flag)
    claude_verbose: bool
    claude_add_dirs: Optional[str]  # Comma-separated list of directories to add
    claude_initial_path: Optional[str]  # Initial working directory for Claude
    mcp_config_path: Optional[str] = None  # Path to MCP config JSON file to load
    
    # Attachments handling (optional)
    attachments_max_size_mb: Optional[int] = None
    attachments_allowed_types: Optional[str] = None  # comma-separated or None
    
    # New whitelist-only permission system
    permissions: Optional[Permissions] = None
    
    # Deprecated options (kept for backward compatibility)
    claude_allowed_tools: Optional[str] = None
    claude_disallowed_tools: Optional[str] = None
    permission_mode: Optional[str] = None
    enabled_mcp_servers: Optional[str] = None
    disabled_mcp_servers: Optional[str] = None
    # Obsidian-System specific controls
    obsidian_allowed_projects: Optional[str] = None

@dataclass
class BotConfig:
    """Complete bot configuration combining all settings."""
    slack: SlackConfig
    app: AppConfig
    channels: Dict[str, ChannelConfig] = field(default_factory=dict)
    scheduled_messages: Dict[str, Any] = field(default_factory=dict)
    
    def get_channel_config(self, channel_id: str) -> AppConfig:
        """Get effective configuration for a specific channel."""
        # Handle session IDs that include "slack_" prefix
        clean_channel_id = channel_id.replace("slack_", "") if channel_id.startswith("slack_") else channel_id
        
        if clean_channel_id in self.channels:
            return self.channels[clean_channel_id].merge_with_global(self.app)
        return self.app

def _interpolate_env_vars(text: str, config_dir: Optional[Path] = None) -> str:
    """Replace ${VAR} and ${file:path} patterns with environment variable values and file contents."""
    # First, expand ~ and $HOME
    expanded = os.path.expanduser(text)
    expanded = os.path.expandvars(expanded)
    
    # Handle ${file:path} patterns first
    def replace_file(match):
        file_path = match.group(1)
        try:
            # Resolve path relative to config file directory
            if config_dir:
                resolved_path = (config_dir / file_path).resolve()
            else:
                resolved_path = Path(file_path).resolve()
            
            # Security checks
            if not resolved_path.exists():
                raise ValueError(f"File not found: {file_path}")
            
            if not resolved_path.is_file():
                raise ValueError(f"Path is not a file: {file_path}")
            
            # Check file size (limit to 1MB)
            file_size = resolved_path.stat().st_size
            if file_size > 1024 * 1024:  # 1MB
                raise ValueError(f"File too large (max 1MB): {file_path}")
            
            # Prevent path traversal attacks by ensuring resolved path is within allowed areas
            if config_dir:
                try:
                    resolved_path.relative_to(config_dir.parent)
                except ValueError:
                    # If not under config parent, check if it's an absolute path we should allow
                    pass
            
            # Read file content and strip trailing whitespace
            content = resolved_path.read_text(encoding='utf-8').rstrip()
            return content
            
        except Exception as e:
            raise ValueError(f"Failed to read file '{file_path}': {e}")
    
    # Apply file interpolation
    expanded = re.sub(r'\$\{file:([^}]+)\}', replace_file, expanded)
    
    # Then handle ${VAR} patterns for environment variables
    def replace_var(match):
        var_name = match.group(1)
        # Skip file: patterns as they're already handled
        if var_name.startswith('file:'):
            return match.group(0)
        return os.environ.get(var_name, match.group(0))  # Return original if not found
    
    return re.sub(r'\$\{([^}]+)\}', replace_var, expanded)

def _interpolate_config(config_dict: Dict[str, Any], config_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Recursively interpolate environment variables and file contents in config dictionary."""
    if isinstance(config_dict, dict):
        return {k: _interpolate_config(v, config_dir) for k, v in config_dict.items()}
    elif isinstance(config_dict, str):
        return _interpolate_env_vars(config_dict, config_dir)
    else:
        return config_dict

def _validate_claude_initial_path(path: str, project_root: Path) -> str:
    """Validate and resolve claude_initial_path securely."""
    if not path:
        return None
    
    try:
        path_obj = Path(path).resolve()
        
        # Basic security checks
        if not path_obj.exists():
            raise ValueError(f"claude_initial_path must be an existing directory: {path}")
            
        if not path_obj.is_dir():
            raise ValueError(f"claude_initial_path must be a directory: {path}")
        
        # Optional: Block obvious system directories for safety (can be removed if too restrictive)
        dangerous_paths = ['/etc', '/usr', '/bin', '/sbin', '/boot', '/sys', '/proc']
        if any(str(path_obj).startswith(danger) for danger in dangerous_paths):
            raise ValueError(f"claude_initial_path cannot be in system directory: {path}")
        
        return str(path_obj)
        
    except Exception as e:
        if "claude_initial_path" in str(e):
            raise  # Re-raise our custom error messages
        else:
            raise ValueError(f"Invalid claude_initial_path '{path}': {str(e)}")

def _validate_claude_max_turns(value) -> Optional[int]:
    """Validate claude_max_turns value."""
    if value is None:
        return None
    
    # Handle string values from JSON config
    if isinstance(value, str):
        if value.lower() in ('null', 'none', ''):
            return None
        try:
            value = int(value)
        except ValueError:
            raise ValueError(f"claude_max_turns must be a positive integer, got: {value}")
    
    # Validate integer value
    if not isinstance(value, int):
        raise ValueError(f"claude_max_turns must be an integer, got: {type(value).__name__}")
    
    if value <= 0:
        raise ValueError(f"claude_max_turns must be positive, got: {value}")
    
    if value > 100:  # Reasonable upper limit
        raise ValueError(f"claude_max_turns too high (max 100), got: {value}")
    
    return value

def _find_config_file(project_root: Path) -> Optional[Path]:
    """Find slackbot_config.yaml or slackbot_config.yml in project root or parent directories."""
    current = project_root.resolve()
    
    # Check current directory first
    for filename in ['slackbot_config.yaml', 'slackbot_config.yml']:
        config_path = current / filename
        if config_path.exists():
            return config_path
    
    # Check parent directories up to root
    for parent in current.parents:
        for filename in ['slackbot_config.yaml', 'slackbot_config.yml']:
            config_path = parent / filename
            if config_path.exists():
                return config_path
    
    return None

def _parse_permissions(permissions_data: Optional[Dict[str, Any]]) -> Optional[Permissions]:
    """Parse permissions configuration from YAML data."""
    if not permissions_data:
        return None
    
    return Permissions(
        tools=permissions_data.get('tools'),
        mcp=permissions_data.get('mcp'),
        mode=permissions_data.get('mode', 'default')
    )

def _load_config_file(config_path: Path) -> Dict[str, Any]:
    """Load and validate YAML configuration file."""
    try:
        # Security checks
        if config_path.is_symlink():
            raise ValueError(f"Config file cannot be a symlink: {config_path}")
        
        # Check file permissions (should not be world-writable)
        import stat
        file_stat = config_path.stat()
        if file_stat.st_mode & stat.S_IWOTH:
            raise ValueError(f"Config file {config_path} is world-writable - security risk")
        
        with open(config_path, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f)
        
        # Interpolate environment variables and file contents
        config_data = _interpolate_config(config_data, config_path.parent)
        
        return config_data
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in config file {config_path}: {e}")
    except Exception as e:
        raise ValueError(f"Error loading config file {config_path}: {e}")

def _create_app_config(global_data: Dict[str, Any], config_dir: Path) -> AppConfig:
    """Create AppConfig from global configuration data.
    
    Args:
        global_data: Global configuration dictionary
        config_dir: Directory where the config file is located
    """
    # Resolve project root relative to config file directory
    if 'project_root' in global_data:
        project_root_str = global_data['project_root']
        if project_root_str == ".":
            # "." means the config file's directory
            project_root = config_dir.resolve()
        elif not Path(project_root_str).is_absolute():
            # Relative path - resolve relative to config directory
            project_root = (config_dir / project_root_str).resolve()
        else:
            # Absolute path
            project_root = Path(project_root_str).resolve()
    else:
        # Default to config file's directory
        project_root = config_dir.resolve()
    
    # Resolve log directory  
    log_dir_str = global_data.get('log_dir', 'slackbot_logs')
    log_dir_path = Path(log_dir_str)
    if not log_dir_path.is_absolute():
        log_dir = (project_root / log_dir_path).resolve()
    else:
        log_dir = log_dir_path.resolve()
    
    # Ensure log directory exists
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Validate claude_initial_path if provided
    claude_initial_path = global_data.get('claude_initial_path')
    if claude_initial_path:
        claude_initial_path = _validate_claude_initial_path(claude_initial_path, project_root)
    
    # Validate claude_max_turns if provided
    claude_max_turns = _validate_claude_max_turns(global_data.get('claude_max_turns'))
    
    # Attachments settings (optional)
    attachments_cfg = global_data.get('attachments') or {}
    attachments_max_size_mb = attachments_cfg.get('max_size_mb')
    allowed_types = attachments_cfg.get('allowed_types')
    if isinstance(allowed_types, list):
        attachments_allowed_types = ','.join([str(t).strip() for t in allowed_types if str(t).strip()])
    else:
        attachments_allowed_types = str(allowed_types).strip() if allowed_types else None

    return AppConfig(
        project_root=project_root,
        log_dir=log_dir,
        claude_command=global_data.get('claude_command', 'claude'),
        max_response_length=global_data.get('max_response_length', 3900),
        system_prompt=global_data.get('system_prompt'),
        claude_model=global_data.get('claude_model'),
        claude_max_turns=claude_max_turns,
        claude_verbose=global_data.get('claude_verbose', False),
        claude_add_dirs=global_data.get('claude_add_dirs'),
        claude_initial_path=claude_initial_path,
        mcp_config_path=global_data.get('mcp_config_path'),
        attachments_max_size_mb=attachments_max_size_mb,
        attachments_allowed_types=attachments_allowed_types,
        # New permissions system
        permissions=_parse_permissions(global_data.get('permissions')),
        # Deprecated options (backward compatibility)
        claude_allowed_tools=global_data.get('claude_allowed_tools'),
        claude_disallowed_tools=global_data.get('claude_disallowed_tools'),
        permission_mode=global_data.get('permission_mode'),
        enabled_mcp_servers=global_data.get('enabled_mcp_servers'),
        disabled_mcp_servers=global_data.get('disabled_mcp_servers'),
        obsidian_allowed_projects=global_data.get('obsidian_allowed_projects')
    )

def _create_channel_configs(channels_data: Dict[str, Any], project_root: Path) -> Dict[str, ChannelConfig]:
    """Create channel configurations from YAML data."""
    channel_configs = {}
    for channel_id, channel_data in channels_data.items():
        # Skip example channels that start with underscore
        if channel_id.startswith('_'):
            continue
        
        # Validate claude_initial_path if provided for this channel
        claude_initial_path = channel_data.get('claude_initial_path')
        if claude_initial_path:
            claude_initial_path = _validate_claude_initial_path(claude_initial_path, project_root)
        
        # Validate claude_max_turns if provided for this channel
        claude_max_turns = _validate_claude_max_turns(channel_data.get('claude_max_turns'))
            
        channel_configs[channel_id] = ChannelConfig(
            system_prompt=channel_data.get('system_prompt'),
            claude_model=channel_data.get('claude_model'),
            claude_max_turns=claude_max_turns,
            claude_verbose=channel_data.get('claude_verbose'),
            claude_add_dirs=channel_data.get('claude_add_dirs'),
            claude_initial_path=claude_initial_path,
            mcp_config_path=channel_data.get('mcp_config_path'),
            max_response_length=channel_data.get('max_response_length'),
            # New permissions system
            permissions=_parse_permissions(channel_data.get('permissions')),
            # Deprecated options (backward compatibility)
            claude_allowed_tools=channel_data.get('claude_allowed_tools'),
            claude_disallowed_tools=channel_data.get('claude_disallowed_tools'),
            permission_mode=channel_data.get('permission_mode'),
            enabled_mcp_servers=channel_data.get('enabled_mcp_servers'),
            disabled_mcp_servers=channel_data.get('disabled_mcp_servers'),
            obsidian_allowed_projects=channel_data.get('obsidian_allowed_projects')
        )
    
    return channel_configs

# Global configuration instance
_config: Optional[BotConfig] = None
_config_file_path: Optional[Path] = None

def load_config(project_root: Optional[str] = None) -> BotConfig:
    """Load configuration from YAML file."""
    global _config, _config_file_path
    
    # Determine project root
    if project_root:
        root_path = Path(project_root).resolve()
    else:
        root_path = Path.cwd().resolve()
    
    # Find config file
    config_path = _find_config_file(root_path)
    if not config_path:
        raise ValueError(f"No slackbot_config.yaml found in {root_path} or parent directories")
    
    print(f"Loading configuration from: {config_path}")
    _config_file_path = config_path
    
    # Load and parse config
    config_data = _load_config_file(config_path)
    
    # Validate required sections
    if 'slack' not in config_data:
        raise ValueError("Config file missing required 'slack' section")
    if 'global' not in config_data:
        raise ValueError("Config file missing required 'global' section")
    
    # Create Slack config
    slack_data = config_data['slack']
    bot_token = slack_data.get('bot_token')
    app_token = slack_data.get('app_token')
    
    if not bot_token:
        raise ValueError("SLACK_BOT_TOKEN is required (set in environment or config file)")
    if not app_token:
        raise ValueError("SLACK_APP_TOKEN is required (set in environment or config file)")
    
    slack_config = SlackConfig(bot_token=bot_token, app_token=app_token)
    
    # Create app config
    # Use the config file's parent directory as the base for relative paths
    config_dir = config_path.parent
    app_config = _create_app_config(config_data['global'], config_dir)
    
    # Create channel configs
    channels_data = config_data.get('channels', {})
    channel_configs = _create_channel_configs(channels_data, app_config.project_root)
    
    # Load scheduled messages config
    scheduled_messages_data = config_data.get('scheduled_messages', {})
    
    _config = BotConfig(
        slack=slack_config,
        app=app_config,
        channels=channel_configs,
        scheduled_messages=scheduled_messages_data
    )
    
    return _config

def get_config() -> BotConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = load_config()
    return _config

def reload_config() -> BotConfig:
    """Reload configuration from file."""
    global _config
    _config = None
    return get_config()

def get_config_file_path() -> Optional[Path]:
    """Get the path to the currently loaded config file."""
    return _config_file_path

def validate_config() -> None:
    """Validate that all required configuration is present."""
    try:
        config = get_config()
        
        # Check if Claude command is available
        import shutil
        if not shutil.which(config.app.claude_command):
            raise ValueError(f"Claude command '{config.app.claude_command}' not found in PATH")
            
        # Check if project root exists
        if not config.app.project_root.exists():
            raise ValueError(f"Project root directory '{config.app.project_root}' does not exist")
        
        # Validate Slack tokens
        if not config.slack.bot_token or config.slack.bot_token.startswith('${'):
            raise ValueError("SLACK_BOT_TOKEN environment variable is required")
        if not config.slack.app_token or config.slack.app_token.startswith('${'):
            raise ValueError("SLACK_APP_TOKEN environment variable is required")
            
    except Exception as e:
        raise ValueError(f"Configuration validation failed: {e}")

def print_config_status() -> None:
    """Print configuration status for debugging."""
    try:
        config = get_config()
        config_path = get_config_file_path()
        
        print("Configuration Status:")
        print(f"  ✓ Config File: {config_path}")
        print(f"  ✓ Slack Bot Token: {'Set' if config.slack.bot_token and not config.slack.bot_token.startswith('${') else 'Missing'}")
        print(f"  ✓ Slack App Token: {'Set' if config.slack.app_token and not config.slack.app_token.startswith('${') else 'Missing'}")
        print(f"  ✓ Project Root: {config.app.project_root}")
        print(f"  ✓ Log Directory: {config.app.log_dir}")
        print(f"  ✓ Claude Command: {config.app.claude_command}")
        print(f"  ✓ Max Response Length: {config.app.max_response_length}")
        print(f"  ✓ System Prompt: {'Set' if config.app.system_prompt else 'Default'}")
        print(f"  ✓ Configured Channels: {len(config.channels)}")
        
        if config.channels:
            for channel_id in config.channels.keys():
                print(f"    - {channel_id}")
        
        # Check Claude availability
        import shutil
        claude_available = shutil.which(config.app.claude_command) is not None
        print(f"  ✓ Claude Available: {'Yes' if claude_available else 'No'}")
        
    except Exception as e:
        print(f"Configuration Error: {e}")
