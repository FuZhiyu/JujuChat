# Development Guidelines for JujuChat

This document provides guidelines for AI agents (Claude, etc.) and developers working on the JujuChat codebase.

## Package Structure Conventions

JujuChat follows standard Python packaging conventions:

- **Package Name**: `jujuchat` (for pip/uv installation)
- **Import Name**: `jujuchat` (in Python code)
- **Development Virtual Environment**: `~/.venv/jujuchat` (for isolated development)

The package is structured as:
```
JujuChat/
├── pyproject.toml          # Package configuration
├── src/
│   └── jujuchat/          # Importable package
│       ├── __init__.py
│       ├── core/          # Shared backend
│       ├── adapters/      # Platform integrations
│       ├── servers/       # HTTP servers
│       └── utils/         # Shared utilities
└── tests/
```

## Development Environments

### Standalone Module Development

When developing JujuChat as a standalone module:

```bash
cd /Users/zhiyufu/Dropbox/Juju/modules/JujuChat

# Set up isolated environment
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv sync

# Run services
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run python -m jujuchat.adapters.slack
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run python -m jujuchat.adapters.rcs
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run python -m jujuchat.servers.http --config rcs_config.yaml

# Run tests
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run pytest

# Development with auto-reload (HTTP server)
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run uvicorn jujuchat.servers.http:app --reload
```

### Integration with Master Juju Environment

When working within the larger Juju system:

```bash
# From the Juju root directory
cd ~/Dropbox/Juju
export UV_PROJECT_ENVIRONMENT="$HOME/.venv/juju"
uv sync  # Installs jujuchat in editable mode

# Use the juju CLI wrapper
juju start --service jujuchat-slack
juju start --service jujuchat-rcs
juju start --service jujuchat-http

# Check status
juju status

# View logs
juju logs --service jujuchat-slack

# Resync after module changes
uv sync
```

## Architecture Understanding

### Core Backend (`jujuchat.core`)

- **ChatBackend**: Main interface to Claude Code via the Agent SDK
  - Manages persistent sessions with session-specific locking
  - Handles streaming responses and event processing
  - Configuration-driven session management
  - Located in `src/jujuchat/core/core.py`

- **ConfigProvider**: Protocol for adapter-specific configuration
  - Allows each adapter to provide its own configuration format
  - Returns session-specific config including MCP settings, tool permissions, etc.

- **Logging System**: Two-layer architecture
  - **CoreLogger**: Session-based Claude API interaction logs
  - **AdapterLogger**: Platform-specific operational logs

### Adapters (`jujuchat.adapters`)

#### Slack Adapter (`jujuchat.adapters.slack`)
- **Architecture**: Direct Python integration with core backend
- **Connection**: Socket Mode for real-time messaging
- **Key Features**:
  - Thread context management
  - File attachment handling (upload/download)
  - Streaming response updates
  - User name caching
  - Commands: `!help`, `!reset`, `!status`, `!history`, `!sendfile`
- **Configuration**: Uses `slackbot_config.yaml`
- **Entry Point**: `python -m jujuchat.adapters.slack`

#### RCS Adapter (`jujuchat.adapters.rcs`)
- **Architecture**: HTTP client to core backend (security isolation)
- **Connection**: Twilio webhook handler for RCS messaging
- **Key Features**:
  - Signature validation
  - Media attachment support
  - Rate limiting middleware
  - Message deduplication
  - Background message processing
- **Configuration**: Uses `.env` file with Twilio credentials
- **Entry Point**: `python -m jujuchat.adapters.rcs`
- **Security**: Runs as separate process, HTTP boundary provides isolation

### HTTP Server (`jujuchat.servers.http`)

- Unified server for multiple purposes
- Provides core chat API endpoints (`/chat`, `/health`, `/attachments`)
- RCS webhook endpoints under `/rcs/*`
- Generic design for future iOS/web client support
- FastAPI with CORS and validation
- Entry Point: `python -m jujuchat.servers.http`

## Logging System

### Architecture Design

JujuChat implements a two-layer logging architecture:

#### Layer 1: Core Logging (`CoreLogger`)
Session-based logging for Claude API interactions, shared across all adapters:
```
logs/jujuchat-core/{session_id}/
├── claude_raw_YYYY-MM-DD.jsonl       # Raw Claude API requests/responses
├── conversations_YYYY-MM-DD.jsonl    # Processed conversation summaries
└── errors_YYYY-MM-DD.jsonl           # Session-specific errors
```

#### Layer 2: Adapter Logging (`AdapterLogger`)
Platform-specific operational logging:
```
logs/jujuchat-{adapter}/
├── operations_YYYY-MM-DD.log         # Adapter operations (startup, config, etc.)
└── events_YYYY-MM-DD.log             # Platform events (webhooks, messages, etc.)
```

### Session ID Format
Standardized session identifiers: `{adapter}_{sanitized_identifier}`
- Slack: `slack_D098GMJR48H` (channel ID)
- RCS: `rcs_15551234567` (phone number, sanitized)
- HTTP: `http_{session_token}` (generated session)

### Migration from Legacy Logging
- **Deprecated**: `slackbot_logs/` directory (old Slack adapter logging)
- **Migrated**: All logging now uses centralized `logs/` directory
- **Backward Compatible**: Existing `BotLogger` API maintained for compatibility

## Configuration Management

Each adapter uses its own configuration system:

### Slack Configuration (`slackbot_config.yaml`)
- Bot tokens (bot_token, app_token)
- Channel-specific settings
- MCP server permissions
- Tool allowlists
- Project paths and system prompts
- Scheduled messages

### RCS Configuration (`.env`)
- Twilio credentials
- Webhook settings
- Claude backend URL
- Attachment settings
- Rate limiting parameters

### HTTP Server Configuration (`server_config.yaml`)
- Host and port settings
- CORS configuration
- Backend service URLs

## Security Considerations

### RCS Security Model
- RCS adapter runs as separate process handling untrusted webhooks
- HTTP boundary provides isolation between internet-facing RCS and core backend
- No direct Python import to core (maintains security isolation)
- Signature validation on all incoming webhooks
- Host header validation
- Rate limiting per client IP

### Slack Security Model
- Slack adapter uses direct Python import (trusted environment)
- Socket Mode connections validated by Slack infrastructure
- No internet-exposed endpoints
- File size and type validation for attachments

## Testing Guidelines

```bash
# Run all tests
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run pytest

# Run specific test file
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run pytest tests/test_core.py

# Run with coverage
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run pytest --cov=jujuchat
```

## Common Development Tasks

### Adding a New Adapter

1. Create a new directory under `src/jujuchat/adapters/{adapter_name}/`
2. Implement the adapter using `ChatBackend` from `jujuchat.core`
3. Create a `ConfigProvider` implementation for your adapter
4. Add configuration file format (YAML, .env, etc.)
5. Create entry point in `__main__.py`
6. Add script entry to `pyproject.toml`
7. Update this documentation

### Adding a New Feature to Slack Adapter

1. Review existing code in `src/jujuchat/adapters/slack/`
2. For message processing: modify `message_processor.py`
3. For commands: add to `bot.py` event handlers
4. For streaming: update `streaming.py`
5. Test with local Slack workspace
6. Update configuration documentation

### Modifying Core Backend Behavior

1. Core backend is in `src/jujuchat/core/core.py`
2. Changes affect all adapters - test thoroughly
3. Ensure backward compatibility with existing adapters
4. Update configuration protocol if needed
5. Update all adapter implementations if protocol changes

## Migration Notes

This module consolidates the previous separate modules:
- `claude_backend` → `jujuchat.core`
- `slackbot` → `jujuchat.adapters.slack`
- `rcs_adapter` → `jujuchat.adapters.rcs`

See `JujuChat-Migration-Tracker.md` for detailed migration progress.

## Best Practices

1. **Use the logging system**:
   - Always use `CoreLogger` for Claude interactions and `AdapterLogger` for adapter-specific events
   - When using standard Python `logging`, pass structured data via the `extra` parameter:
     ```python
     logger.info("File uploaded", extra={"session_id": session_id, "file_path": path})
     ```
   - Do NOT pass kwargs directly: ~~`logger.info("Message", session_id=id)`~~ (will raise TypeError)

2. **Session management**: Let `ChatBackend` handle session lifecycle, don't manage Claude processes directly

3. **Configuration**: Use the `ConfigProvider` protocol for all config access

4. **Error handling**: Wrap Claude interactions in try/except and provide user-friendly error messages

5. **Streaming**: Implement streaming responses for better UX (see Slack adapter for reference)

6. **Security**: Never log sensitive data (tokens, credentials, personal information)

7. **Testing**: Write tests for new features, especially for core backend changes

## File Locations Reference

- **Core Backend**: `src/jujuchat/core/`
  - `core.py`: ChatBackend implementation
  - `config.py`: Configuration protocols
  - `logging.py`: Logging system
  - `models.py`: Data models
  - `history.py`: Conversation history management

- **Slack Adapter**: `src/jujuchat/adapters/slack/`
  - `bot.py`: Main bot application
  - `message_processor.py`: Message handling
  - `streaming.py`: Streaming response updates
  - `attachments.py`: File handling
  - `config.py`: Configuration management

- **RCS Adapter**: `src/jujuchat/adapters/rcs/`
  - `adapter.py`: Main FastAPI application
  - `config.py`: Configuration and models
  - `media_handler.py`: Media attachment handling
  - `twilio_validator.py`: Webhook signature validation

- **HTTP Server**: `src/jujuchat/servers/http/`
  - `server.py`: Unified HTTP server
  - `__main__.py`: Entry point

## Troubleshooting

### Common Issues

1. **Import errors**: Ensure you're in the correct virtual environment
2. **Claude subprocess failures**: Check `claude_command` path in config
3. **MCP server not loading**: Verify `.claude/settings.local.json` exists and is valid
4. **Slack connection issues**: Check bot tokens and Socket Mode is enabled
5. **RCS webhook failures**: Verify Twilio signature validation and host header
6. **File attachment issues**: Check file size limits and allowed types in config
