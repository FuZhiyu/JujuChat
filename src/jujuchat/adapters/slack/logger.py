#%% Unified Slack Adapter Logger
"""
Logging module for the Slack Claude Bot using the unified JujuChat logging system.

This module provides adapter-specific logging for Slack events and operations.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
import json
from pathlib import Path

from ...core.logging import get_adapter_logger, create_session_id
from .config import AppConfig
from .exceptions import LoggingError


class BotLogger:
    """
    Handles logging operations for the Slack Claude Bot using unified logging.
    
    This class provides backward compatibility while using the new 
    AdapterLogger underneath for Slack-specific operations.
    """
    
    def __init__(self, config: AppConfig):
        """
        Initialize BotLogger with unified logging system.
        
        Args:
            config: Application configuration (log_dir will be ignored)
        """
        # Use the new unified logging system
        self.adapter_logger = get_adapter_logger("slack")
        print(f"BotLogger initialized with unified logging system")
    
    async def log_message(self, user_id: str, channel: str, message: str, message_type: str) -> None:
        """
        Log a message using the unified logging system.
        
        Args:
            user_id: Slack user ID
            channel: Slack channel ID
            message: Message content
            message_type: Type of message (incoming, outgoing, etc.)
            
        Raises:
            LoggingError: When logging operation fails
        """
        try:
            # Create session ID for the channel
            session_id = create_session_id("slack", channel)
            
            # Log as an event using the unified system
            await self.adapter_logger.log_event(
                "message",
                {
                    "user_id": user_id,
                    "channel": channel,
                    "session_id": session_id,
                    "message": message,
                    "message_type": message_type,
                    "message_length": len(message)
                }
            )
            
        except Exception as e:
            raise LoggingError(f"Failed to log message: {str(e)}")
    
    async def log_error(self, user_id: str, channel: str, error: str) -> None:
        """
        Log errors using the unified logging system.
        
        Args:
            user_id: Slack user ID
            channel: Slack channel ID
            error: Error message or description
            
        Raises:
            LoggingError: When logging operation fails
        """
        try:
            # Create session ID for the channel
            session_id = create_session_id("slack", channel)
            
            # Log as an operation with ERROR level
            await self.adapter_logger.log_operation(
                "slack_error",
                {
                    "user_id": user_id,
                    "channel": channel,
                    "session_id": session_id,
                    "error": error
                },
                level="ERROR"
            )
            
        except Exception as e:
            # Don't raise LoggingError here to avoid recursive error logging
            print(f"Failed to log error: {str(e)}")
    
    async def get_conversation_history(self, channel: str, limit: int = 10, date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieve recent conversation history for a channel.
        
        Note: This method is deprecated in favor of the unified logging system.
        It now returns an empty list as conversation history should be retrieved
        from the session-based logs in logs/jujuchat-core/{session_id}/
        
        Args:
            channel: Slack channel ID
            limit: Maximum number of messages to return
            date: Specific date to retrieve (YYYY-MM-DD format), defaults to today
            
        Returns:
            Empty list (deprecated functionality)
        """
        # This functionality is deprecated - conversation logs are now in
        # the session-based core logging system
        print(f"get_conversation_history is deprecated - check logs/jujuchat-core/slack_{channel}/")
        return []
    
    async def get_error_logs(self, limit: int = 10, date: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Retrieve recent error logs.
        
        Note: This method is deprecated in favor of the unified logging system.
        Error logs are now in logs/jujuchat-slack/operations_YYYY-MM-DD.log
        
        Args:
            limit: Maximum number of errors to return
            date: Specific date to retrieve (YYYY-MM-DD format), defaults to today
            
        Returns:
            Empty list (deprecated functionality)
        """
        print(f"get_error_logs is deprecated - check logs/jujuchat-slack/operations_*.log")
        return []
    
    def get_log_stats(self, date: Optional[str] = None) -> Dict[str, int]:
        """
        Get logging statistics for a specific date.
        
        Note: This method is deprecated in favor of the unified logging system.
        
        Args:
            date: Date to get stats for (YYYY-MM-DD format), defaults to today
            
        Returns:
            Dictionary with zero counts (deprecated functionality)
        """
        date_str = date or datetime.now().strftime("%Y-%m-%d")
        
        return {
            "conversations": 0,
            "errors": 0,
            "date": date_str,
            "note": "Stats moved to unified logging system - check logs/jujuchat-slack/"
        }