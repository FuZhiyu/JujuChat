# JujuChat

**Unified chat integration module for the Juju personal AI assistant system**

JujuChat provides a flexible, adapter-based architecture for integrating Claude AI across multiple chat platforms. It features a shared core backend powered by the Claude Agent SDK, with platform-specific adapters for Slack, RCS (via Twilio), and a generic HTTP API for future integrations.

## Features

### Core Backend
- **Persistent Sessions**: Maintains conversation context across multiple interactions
- **Streaming Responses**: Real-time message streaming for better user experience
- **Configuration-Driven**: Flexible per-session configuration for different use cases
- **MCP Integration**: Full support for Model Context Protocol servers
- **Tool Management**: Granular control over allowed/disallowed tools
- **Comprehensive Logging**: Two-layer logging system for both Claude interactions and adapter operations

### Platform Adapters

#### Slack Adapter (Most Feature-Rich)
The Slack adapter is the most mature and feature-complete implementation:

- **Direct Integration**: Native Python integration with core backend for optimal performance
- **Socket Mode**: Real-time bidirectional communication with Slack
- **Advanced Features**:
  - Thread context management (automatically includes conversation history)
  - Streaming message updates (messages update in real-time as Claude responds)
  - File attachments (upload/download with configurable size and type restrictions)
  - User name resolution with caching
  - Multiple interaction modes: DMs, channel mentions, threaded conversations
  - Bot commands: `!help`, `!reset`, `!interrupt`, `!status`, `!config`, `!history`, `!compact`, `!auto-compact`, `!reload-config`, `!schedule`, `!sendfile`
  - Scheduled messages support
  - Automatic session cleanup
- **Configuration**: YAML-based with channel-specific settings and MCP permissions

#### RCS Adapter (Production-Ready)
The RCS adapter provides secure messaging via Twilio:

- **Security-First Design**: Isolated HTTP process for handling untrusted webhooks
- **Twilio Integration**: Full webhook validation and media handling
- **Key Features**:
  - Webhook signature validation
  - Media attachment support (images, videos, documents)
  - Rate limiting middleware
  - Message deduplication
  - Background message processing
  - Host header validation
- **Configuration**: Environment-based (.env) for easy deployment

#### HTTP Server Adapter
**Note**: The HTTP server adapter is currently not up to date compared to the Slack adapter. It provides basic functionality but lacks many advanced features like streaming updates, comprehensive file handling, and thread management.

- **Generic Chat API**: RESTful endpoints for chat interactions
- **RCS Webhook Support**: Dedicated endpoints for Twilio webhooks
- **Future-Ready**: Designed for iOS and web client integration
- **FastAPI-Based**: Modern async Python web framework with automatic OpenAPI docs

## Architecture

```
jujuchat/
├── core/                    # Shared Claude backend
│   ├── core.py             # ChatBackend (main Claude integration)
│   ├── config.py           # Configuration protocols
│   ├── logging.py          # Two-layer logging system
│   ├── models.py           # Data models
│   └── history.py          # Conversation history management
│
├── adapters/               # Platform-specific integrations
│   ├── slack/             # Slack bot (most feature-rich)
│   │   ├── bot.py         # Main application & event handlers
│   │   ├── message_processor.py  # Message handling & commands
│   │   ├── streaming.py   # Real-time message updates
│   │   ├── attachments.py # File upload/download
│   │   └── config.py      # YAML configuration management
│   │
│   └── rcs/               # RCS messaging via Twilio
│       ├── adapter.py     # FastAPI webhook handler
│       ├── media_handler.py      # Media attachment processing
│       ├── twilio_validator.py   # Signature validation
│       └── config.py      # Environment-based configuration
│
└── servers/               # HTTP API servers
    └── http/              # Unified HTTP server (not fully up to date)
        └── server.py      # Core chat API & RCS webhooks
```

## Quick Start

### Prerequisites

1. **Python 3.10+** installed
2. **Claude Code CLI** installed and accessible:
   ```bash
   # Check Claude Code installation
   claude --version
   ```
   If not installed, follow the [official installation guide](https://docs.anthropic.com/en/docs/claude-code).

3. **Node.js** (required for Claude Code)

## Installation

### Standalone Development

```bash
cd /path/to/JujuChat

# Set up isolated environment
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv sync

# Run services
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run jujuchat-slack
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run jujuchat-rcs
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run jujuchat-http --config server_config.yaml
```

### Integration with Juju System

```bash
# From the Juju root directory
cd ~/Dropbox/Juju
export UV_PROJECT_ENVIRONMENT="$HOME/.venv/juju"
uv sync  # Installs jujuchat in editable mode

# Use the juju CLI
juju start --service jujuchat-slack
juju start --service jujuchat-rcs
juju status
```

## Configuration

### Slack Adapter
Configuration file: `slackbot_config.yaml`

```yaml
slack:
  bot_token: xoxb-...
  app_token: xapp-...

app:
  project_root: /path/to/project
  system_prompt: "You are a helpful assistant..."
  claude_model: claude-sonnet-4-5-20250929

  # MCP configuration
  mcp_config_path: .claude/settings.local.json

  # Tool permissions
  permissions:
    mode: default  # default, plan, acceptEdits, bypassPermissions
    tools: [Read, Write, Edit, Bash, Grep, Glob]
    mcp:
      obsidian:
        - list-active-file-info
        - search-daily-notes

  # Attachment settings
  attachments_max_size_mb: 25
  attachments_allowed_types: "image/png,image/jpeg,application/pdf"

# Channel-specific overrides
channels:
  C12345:
    system_prompt: "Custom prompt for this channel"
    claude_model: claude-opus-4-20250514

# Scheduled messages (optional)
scheduled_messages:
  daily_standup:
    time: "0 9 * * 1-5"  # 9 AM on weekdays (Mon-Fri)
    channel: C12345
    prompt: "Generate a brief daily standup summary based on recent activity"
    enabled: true
    timezone: "America/New_York"

  weekly_report:
    time: "0 17 * * 5"  # 5 PM every Friday
    channel: C12345
    prompt: "Create a weekly progress report summarizing this week's work"
    enabled: true
```

### RCS Adapter
Configuration file: `.env`

```env
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_MESSAGING_SERVICE_SID=MG...
TWILIO_WEBHOOK_SECRET_PATH=your-secret-path

CLAUDE_HTTP_URL=http://localhost:8811
PUBLIC_HOSTNAME=rcs.yourdomain.com

ATTACHMENTS_DIR=./attachments
ADAPTER_MAX_BODY_BYTES=10485760
ADAPTER_RATE_LIMIT_RPS=2.0
```

### HTTP Server
Configuration file: `server_config.yaml`

```yaml
host: 0.0.0.0
port: 8811
cors:
  allow_origins: ["*"]
  allow_methods: ["GET", "POST"]
```

## Slack Bot Setup Guide

### 1. Create Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and create a new app "From scratch"
2. Choose a name (e.g., "Juju Assistant") and select your workspace
3. Note your **App ID** for reference

### 2. Enable Socket Mode

1. Navigate to **Socket Mode** in the left sidebar
2. Enable Socket Mode
3. Generate an **App-Level Token** with `connections:write` scope
4. Save this token as your `SLACK_APP_TOKEN`

### 3. Configure Bot Permissions

Navigate to **OAuth & Permissions** and add these **Bot Token Scopes**:

**Required for basic functionality:**
- `app_mentions:read` - Read messages that mention the bot
- `chat:write` - Send messages as the bot
- `im:history` - View direct message history
- `im:read` - View direct message info
- `im:write` - Start direct messages

**Required for thread context (highly recommended):**
- `channels:history` - View message history in public channels
- `groups:history` - View message history in private channels
- `groups:read` - Access private channel information
- `users:read` - Get user display names for better context

**Required for file attachments:**
- `files:read` - Read file information
- `files:write` - Upload files to Slack

### 4. Enable App Home

Navigate to **App Home** and configure:

1. **Enable Messages Tab**: Turn on "Allow users to send Slash commands and messages from the messages tab"
2. **Always Show My Bot as Online**: Enable this option
3. Set your bot's display name and default username

**Important**: Without this step, users will see "Sending messages to this app has been turned off" when trying to DM the bot.

### 5. Subscribe to Events

Navigate to **Event Subscriptions** and:

1. Enable Events
2. Subscribe to these **Bot Events**:
   - `app_mention` - When bot is @mentioned in channels
   - `message.im` - Direct messages to the bot

Socket Mode handles event delivery, so no Request URL is needed.

### 6. Install to Workspace

1. Navigate to **OAuth & Permissions**
2. Click **Install to Workspace**
3. Review and authorize the permissions
4. Copy your **Bot User OAuth Token** (starts with `xoxb-`)
5. Save this as your `SLACK_BOT_TOKEN`

### 7. Set Environment Variables

Create a `.env` file or export environment variables:

```bash
export SLACK_BOT_TOKEN="xoxb-your-bot-token-here"
export SLACK_APP_TOKEN="xapp-your-app-token-here"
```

Or add to your `slackbot_config.yaml`:

```yaml
slack:
  bot_token: "${SLACK_BOT_TOKEN}"
  app_token: "${SLACK_APP_TOKEN}"
```

### 8. Test the Connection

```bash
# Test basic Slack connectivity
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run python -c "
from slack_bolt import App
import os

app = App(token=os.getenv('SLACK_BOT_TOKEN'))
result = app.client.auth_test()
print(f'✅ Connected as: {result[\"user\"]}')
"
```

### 9. Run the Bot

```bash
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run jujuchat-slack /path/to/your/project
```

You should see:
```
🚀 Starting Slack Claude Bot...
✅ Bot user ID: U123456789
✅ Bot is ready to receive messages!
📱 Available in:
   • Direct messages
   • Explicit channel mentions (@bot_name)
   • Threaded messages with explicit mentions
```

### 10. Test in Slack

1. **Direct Message Test**: Open a DM with your bot and send "Hello!"
2. **Channel Test**: Invite the bot to a channel (`/invite @bot-name`) and mention it: `@bot-name help`
3. **Thread Test**: Reply in a thread mentioning the bot

### Troubleshooting

**"Sending messages to this app has been turned off"**
- Go to **App Home** → Enable "Allow users to send Slash commands and messages from the messages tab"
- Reload Slack (`Cmd+R` on Mac, `Ctrl+R` on Windows)

**"Missing scope: channels:history" or "Missing scope: groups:history"**
- Add the missing scopes in **OAuth & Permissions**
- **Reinstall the app** to your workspace to activate new permissions

**Thread context not working**
- Ensure you have `channels:history`, `groups:history`, `groups:read`, and `users:read` scopes
- Reinstall the app after adding scopes

**Bot not responding**
- Check bot is running and shows "✅ Bot is ready to receive messages!"
- Verify Socket Mode is enabled
- Check logs for errors
- Ensure bot is invited to the channel (for channel mentions)

## Unified Logging System

JujuChat implements a sophisticated two-layer logging architecture:

### Layer 1: Core Logging
Session-based logging for Claude API interactions (shared across all adapters):

```
logs/jujuchat-core/{session_id}/
├── claude_raw_YYYY-MM-DD.jsonl       # Raw Claude API requests/responses
├── conversations_YYYY-MM-DD.jsonl    # Conversation summaries
└── errors_YYYY-MM-DD.jsonl           # Errors
```

### Layer 2: Adapter Logging
Platform-specific operational logging:

```
logs/jujuchat-{adapter}/
├── operations_YYYY-MM-DD.log         # Startup, config, operations
└── events_YYYY-MM-DD.log             # Platform events (webhooks, messages)
```

### Session ID Format
- Slack: `slack_D098GMJR48H` (channel ID)
- RCS: `rcs_15551234567` (sanitized phone number)
- HTTP: `http_{session_token}` (generated token)

## Usage Examples

### Slack Bot Commands

```
# Direct message to bot
Hello, can you help me?

# Channel mention
@juju what's the weather like?

# Thread reply with context
@juju can you summarize this discussion?

# Bot commands
!help                      # Show available commands
!reset                     # Reset conversation history
!interrupt                 # Interrupt a long-running response
!status                    # Show session status and statistics
!config                    # Show channel configuration
!history [N]               # Show last N messages (default: 5, max: 20)
!compact                   # Compact the stored conversation history
!auto-compact [on|off]     # Manage automatic history compaction
!reload-config             # Reload configuration from file
!schedule                  # Show scheduled messages status
!schedule enable <name>    # Enable a scheduled message
!schedule disable <name>   # Disable a scheduled message
!sendfile <path ...>       # Upload file(s) from session attachments
```

### HTTP API

```bash
# Send a chat message
curl -X POST http://localhost:8811/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Hello, Claude!",
    "session_id": "http_user123"
  }'

# Upload an attachment
curl -X POST http://localhost:8811/attachments \
  -F "file=@document.pdf" \
  -F "session_id=http_user123"

# Health check
curl http://localhost:8811/health
```

### Python API

```python
from jujuchat.core import ChatBackend, ConfigProvider

# Implement your config provider
class MyConfigProvider(ConfigProvider):
    def get_session_config(self, session_id: str):
        return MySessionConfig()

# Create backend
backend = ChatBackend(MyConfigProvider())

# Send message
response = await backend.send_message_with_session(
    message="Hello, Claude!",
    session_id="my_session"
)
```

## Security

### RCS Security Model
- **Process Isolation**: RCS adapter runs as a separate process
- **HTTP Boundary**: Isolates internet-facing webhooks from core backend
- **Signature Validation**: Validates all Twilio webhook signatures
- **Host Validation**: Ensures requests come from expected domains
- **Rate Limiting**: Prevents abuse via IP-based rate limiting
- **Size Limits**: Enforces maximum payload sizes

### Slack Security Model
- **Trusted Environment**: Direct Python integration (no external exposure)
- **Socket Mode**: Outbound connections only, validated by Slack
- **File Validation**: Size and type restrictions on attachments
- **No Exposed Endpoints**: No internet-facing HTTP endpoints

## Development

For detailed development guidelines, environment setup, testing procedures, and architecture details, see [AGENTS.md](AGENTS.md).

Quick start for development:

```bash
# Clone and setup
cd /path/to/JujuChat
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv sync

# Run tests
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run pytest

# Run with auto-reload (development)
UV_PROJECT_ENVIRONMENT=~/.venv/jujuchat uv run uvicorn jujuchat.servers.http:app --reload
```

## Migration Notes

JujuChat consolidates several previous modules:
- `claude_backend` → `jujuchat.core`
- `slackbot` → `jujuchat.adapters.slack`
- `rcs_adapter` → `jujuchat.adapters.rcs`

See `JujuChat-Migration-Tracker.md` for detailed migration progress.

## Known Limitations

- **HTTP Server Adapter**: Not fully up to date with Slack adapter features. Missing streaming updates, advanced file handling, and comprehensive thread management. The Slack adapter is recommended as the reference implementation for new features.

## License

Part of the Juju personal AI assistant system. For internal use.

## Support

For issues, questions, or feature requests, please contact the Juju system maintainers or refer to the project documentation in the main Juju repository.
