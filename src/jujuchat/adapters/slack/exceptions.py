#%% Custom Exceptions
"""
Custom exception classes for the Slack Claude Bot.

This module defines specific exception types to handle different error
scenarios that can occur during bot operation.
"""

class BotError(Exception):
    """Base exception class for all bot-related errors."""
    pass

class ConfigurationError(BotError):
    """Raised when configuration is invalid or missing."""
    pass

class ClaudeError(BotError):
    """Raised when Claude Code integration fails."""
    pass

class SlackError(BotError):
    """Raised when Slack API operations fail."""
    pass

class MessageProcessingError(BotError):
    """Raised when message processing fails."""
    pass

class LoggingError(BotError):
    """Raised when logging operations fail."""
    pass