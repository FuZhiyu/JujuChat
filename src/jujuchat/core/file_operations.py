"""File operations abstraction for cross-adapter file uploads.

This module provides a unified interface for agents to upload files to different
platforms (Slack, RCS, etc.) without needing to know platform-specific details.

Architecture:
- FileUploadHandler: Protocol defining the upload interface
- FileUploadResult: Standard return type for upload operations
- ChatBackend maintains a registry mapping session prefixes to handlers
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Optional, Dict, Any


@dataclass
class FileUploadResult:
    """Result of a file upload operation."""

    success: bool
    file_path: str
    message: Optional[str] = None
    platform_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "success": self.success,
            "file_path": self.file_path,
            "message": self.message,
            "platform_data": self.platform_data,
            "error": self.error,
        }


class FileUploadHandler(Protocol):
    """Protocol for platform-specific file upload implementations.

    Each adapter (Slack, RCS, HTTP) should implement this protocol to handle
    file uploads for their platform.
    """

    async def upload_file(
        self,
        session_id: str,
        file_path: str,
        *,
        title: Optional[str] = None,
        comment: Optional[str] = None,
        **kwargs
    ) -> FileUploadResult:
        """Upload a file for the given session.

        Args:
            session_id: Session identifier (e.g., 'slack_D098GMJR48H')
            file_path: Path to file within session's attachments directory
            title: Optional title/caption for the file
            comment: Optional comment/message to include with upload
            **kwargs: Platform-specific options

        Returns:
            FileUploadResult with upload status and details

        Raises:
            ValueError: If file validation fails
            RuntimeError: If upload fails
        """
        ...


class FileOperationError(Exception):
    """Base exception for file operation errors."""
    pass


class UnsupportedAdapterError(FileOperationError):
    """Raised when no upload handler is registered for a session's adapter."""
    pass


class FileValidationError(FileOperationError):
    """Raised when file validation fails (path, size, type, etc.)."""
    pass
