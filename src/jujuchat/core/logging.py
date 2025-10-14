"""
Unified logging infrastructure for JujuChat.

Provides session-based logging for Claude API calls and adapter-specific operations.
"""

import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import aiofiles
import logging


class CoreLogger:
    """
    Core logger for Claude API interactions.
    
    Logs all Claude API calls to session-specific directories with the format:
    logs/jujuchat-core/{session_id}/claude_raw_YYYY-MM-DD.jsonl
    """
    
    def __init__(self, base_log_dir: Path):
        self.base_log_dir = Path(base_log_dir)
        self.core_log_dir = self.base_log_dir / "jujuchat-core"
        self._write_locks = {}  # Per-session file locks
        
    def _get_session_dir(self, session_id: str) -> Path:
        """Get the log directory for a specific session."""
        return self.core_log_dir / session_id
    
    def _get_write_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a write lock for a session."""
        if session_id not in self._write_locks:
            self._write_locks[session_id] = asyncio.Lock()
        return self._write_locks[session_id]
    
    async def log_claude_raw(
        self, 
        session_id: str, 
        direction: str, 
        data: Any,
        parent_tool_use_id: Optional[str] = None,
        uuid: Optional[str] = None
    ) -> None:
        """
        Log raw Claude API request or response.
        
        Args:
            session_id: Unique session identifier (e.g., "slack_D098GMJR48H")
            direction: "request" or "response"
            data: Raw data to log (message string for request, event dict for response)
            parent_tool_use_id: Optional parent tool use ID
            uuid: Optional UUID for the log entry
        """
        try:
            # Prepare log directory
            session_dir = self._get_session_dir(session_id)
            session_dir.mkdir(parents=True, exist_ok=True)
            
            # Prepare log file path
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = session_dir / f"claude_raw_{today}.jsonl"
            
            # Prepare log entry
            timestamp = datetime.now().isoformat()
            log_entry = {
                "timestamp": timestamp,
                "session": session_id,
                "direction": direction,
            }
            
            if direction == "request":
                log_entry["message"] = str(data)
            else:  # response
                log_entry["event"] = data
                
            if parent_tool_use_id:
                log_entry["parent_tool_use_id"] = parent_tool_use_id
            if uuid:
                log_entry["uuid"] = uuid
            
            # Write to file with session-specific lock
            lock = self._get_write_lock(session_id)
            async with lock:
                async with aiofiles.open(log_file, 'a', encoding='utf-8') as f:
                    await f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                    
        except Exception as e:
            # Fallback to standard logging if file write fails
            logging.error(f"Failed to write claude_raw log for session {session_id}: {e}")
    
    async def log_conversation(
        self,
        session_id: str,
        conversation_data: Dict[str, Any]
    ) -> None:
        """
        Log conversation summary.
        
        Args:
            session_id: Unique session identifier
            conversation_data: Processed conversation data
        """
        try:
            session_dir = self._get_session_dir(session_id)
            session_dir.mkdir(parents=True, exist_ok=True)
            
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = session_dir / f"conversations_{today}.jsonl"
            
            timestamp = datetime.now().isoformat()
            log_entry = {
                "timestamp": timestamp,
                "session": session_id,
                **conversation_data
            }
            
            lock = self._get_write_lock(session_id)
            async with lock:
                async with aiofiles.open(log_file, 'a', encoding='utf-8') as f:
                    await f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                    
        except Exception as e:
            logging.error(f"Failed to write conversation log for session {session_id}: {e}")
    
    async def log_error(
        self,
        session_id: str,
        error_type: str,
        error_message: str,
        error_details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Log errors for a specific session.
        
        Args:
            session_id: Unique session identifier
            error_type: Type of error (e.g., "api_error", "validation_error")
            error_message: Human-readable error message
            error_details: Additional error context
        """
        try:
            session_dir = self._get_session_dir(session_id)
            session_dir.mkdir(parents=True, exist_ok=True)
            
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = session_dir / f"errors_{today}.jsonl"
            
            timestamp = datetime.now().isoformat()
            log_entry = {
                "timestamp": timestamp,
                "session": session_id,
                "error_type": error_type,
                "error_message": error_message,
                "error_details": error_details or {}
            }
            
            lock = self._get_write_lock(session_id)
            async with lock:
                async with aiofiles.open(log_file, 'a', encoding='utf-8') as f:
                    await f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                    
        except Exception as e:
            logging.error(f"Failed to write error log for session {session_id}: {e}")


class AdapterLogger:
    """
    Base logger for adapter-specific operations.
    
    Each adapter (slack, rcs, http) gets its own operational logs.
    """
    
    def __init__(self, adapter_name: str, base_log_dir: Path):
        self.adapter_name = adapter_name
        self.log_dir = Path(base_log_dir) / f"jujuchat-{adapter_name}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._write_lock = asyncio.Lock()
    
    async def log_operation(
        self,
        operation: str,
        details: Dict[str, Any],
        level: str = "INFO"
    ) -> None:
        """
        Log adapter operations.
        
        Args:
            operation: Name of the operation (e.g., "message_received", "webhook_processed")
            details: Operation details
            level: Log level (INFO, WARNING, ERROR)
        """
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = self.log_dir / f"operations_{today}.log"
            
            timestamp = datetime.now().isoformat()
            log_entry = {
                "timestamp": timestamp,
                "adapter": self.adapter_name,
                "level": level,
                "operation": operation,
                "details": details
            }
            
            async with self._write_lock:
                async with aiofiles.open(log_file, 'a', encoding='utf-8') as f:
                    await f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                    
        except Exception as e:
            logging.error(f"Failed to write operation log for {self.adapter_name}: {e}")
    
    async def log_event(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        level: str = "INFO"
    ) -> None:
        """
        Log adapter events (Slack events, RCS webhooks, HTTP requests).
        
        Args:
            event_type: Type of event
            event_data: Event data
            level: Log level
        """
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = self.log_dir / f"events_{today}.log"
            
            timestamp = datetime.now().isoformat()
            log_entry = {
                "timestamp": timestamp,
                "adapter": self.adapter_name,
                "level": level,
                "event_type": event_type,
                "event_data": event_data
            }
            
            async with self._write_lock:
                async with aiofiles.open(log_file, 'a', encoding='utf-8') as f:
                    await f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
                    
        except Exception as e:
            logging.error(f"Failed to write event log for {self.adapter_name}: {e}")


def create_session_id(adapter: str, identifier: str) -> str:
    """
    Create a standardized session ID.
    
    Args:
        adapter: Adapter name (slack, rcs, ios, web)
        identifier: Unique identifier (channel_id, phone_number, device_uuid, etc.)
    
    Returns:
        Formatted session ID (e.g., "slack_D098GMJR48H", "rcs_15551234567")
    """
    # Sanitize identifier to be filesystem-safe
    import re
    sanitized = re.sub(r'[^\w\-]', '', identifier)
    return f"{adapter}_{sanitized}"


# Global logger instances
_core_logger: Optional[CoreLogger] = None

def get_core_logger(base_log_dir: Optional[Path] = None) -> CoreLogger:
    """Get or create the global core logger instance."""
    global _core_logger
    if _core_logger is None:
        if base_log_dir is None:
            base_log_dir = Path.home() / "Dropbox" / "Juju" / "logs"
        _core_logger = CoreLogger(base_log_dir)
    return _core_logger


def get_adapter_logger(adapter_name: str, base_log_dir: Optional[Path] = None) -> AdapterLogger:
    """Get an adapter logger instance."""
    if base_log_dir is None:
        base_log_dir = Path.home() / "Dropbox" / "Juju" / "logs"
    return AdapterLogger(adapter_name, base_log_dir)