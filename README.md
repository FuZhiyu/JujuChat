# JujuChat

Unified chat integration module for the Juju system, consolidating Slack, RCS, and HTTP chat interfaces.

## Package Structure

JujuChat is a Python package following standard conventions:
- **Package Name**: `jujuchat` (for pip/uv)
- **Import Name**: `jujuchat` (in Python code)
- **Virtual Environment**: `~/.venv/jujuchat` (for isolated development)

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

## Architecture

```
JujuChat/
├── core/           # Shared Claude backend & unified logging
│   ├── core.py         # ChatBackend (Claude integration)
│   ├── config.py       # Configuration management
│   └── logging.py      # Unified logging system
├── adapters/       # Chat platform integrations
│   ├── slack/      # Slack bot (direct Python integration)
│   └── rcs/        # RCS adapter (HTTP client for security)
├── servers/
│   └── http/       # HTTP API server
└── utils/          # Shared utilities
```

## Components

### Core Backend
- `ChatBackend`: Manages Claude Code subprocess communication (renamed from ClaudeBackend)
- `ConfigProvider`: Configuration management protocol
- `CoreLogger`: Session-based logging for Claude API interactions
- `AdapterLogger`: Platform-specific logging for adapters
- Session management and persistent conversation history

### Adapters

#### Slack Adapter
- Direct Python integration with core backend
- Socket Mode for real-time messaging
- Thread context and conversation management
- Commands: `!help`, `!reset`, `!status`, `!history`

#### RCS Adapter  
- Twilio webhook handler for RCS messaging
- HTTP client to core backend (security isolation)
- Media attachment support
- Signature validation and rate limiting

### HTTP Server
- Unified server for RCS webhooks
- Generic design for future iOS/web client support
- FastAPI with CORS and validation

## Usage

### Standalone Module Development

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
```

### Integration with Master Juju Environment

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
```

## Unified Logging System

JujuChat implements a two-layer logging architecture:

### Architecture Design

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

### Log Content Examples

**Core Logs (claude_raw_YYYY-MM-DD.jsonl):**
```json
{"timestamp": "2025-09-14T17:08:40.825", "session": "slack_D098GMJR48H", "direction": "request", "message": "Hello"}
{"timestamp": "2025-09-14T17:08:43.426", "session": "slack_D098GMJR48H", "direction": "response", "event": {...}}
```

**Adapter Logs (events_YYYY-MM-DD.log):**
```json
{"timestamp": "2025-09-14T17:08:40.825", "adapter": "slack", "level": "INFO", "event_type": "message_received", "event_data": {...}}
```

### Migration from Legacy Logging
- **Deprecated**: `slackbot_logs/` directory (old Slack adapter logging)
- **Migrated**: All logging now uses centralized `logs/` directory
- **Backward Compatible**: Existing `BotLogger` API maintained for compatibility

## Configuration

Each adapter uses its own configuration system:
- **Slack**: `slackbot_config.yaml` (existing format)
- **RCS**: `.env` file with Twilio credentials
- **HTTP**: Generic YAML config for server settings

## Security

### RCS Security Model
- RCS adapter runs as separate process handling untrusted webhooks
- HTTP boundary provides isolation between internet-facing RCS and core backend
- No direct Python import to core (maintains security isolation)

### Slack Security Model  
- Slack adapter uses direct Python import (trusted environment)
- Socket Mode connections validated by Slack infrastructure
- No internet-exposed endpoints

## Development

### Standalone Development
```bash
cd /Users/zhiyufu/Dropbox/Juju/modules/JujuChat

# Install dependencies in isolated environment
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv sync

# Run tests
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run pytest

# Development with auto-reload
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run uvicorn jujuchat.servers.http:app --reload
```

### Integration with Master Environment
```bash
# From the Juju root directory
cd ~/Dropbox/Juju
export UV_PROJECT_ENVIRONMENT="$HOME/.venv/juju"

# Resync after module changes
uv sync

# Run services via juju CLI
juju start --all
juju logs --service jujuchat-slack
```

## Migration

This module consolidates the previous separate modules:
- `claude_backend` → `jujuchat.core`
- `slackbot` → `jujuchat.adapters.slack`  
- `rcs_adapter` → `jujuchat.adapters.rcs`

See `JujuChat-Migration-Tracker.md` for migration progress and details.