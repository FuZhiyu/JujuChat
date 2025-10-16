"""
Core backend module for JujuChat.

Provides the shared Claude integration backend that all adapters use.

Exports:
- ChatBackend: core persistent backend class (renamed from ClaudeBackend)
- ConfigProvider: protocol for per-session config lookup
- FileUploadHandler: protocol for adapter file upload implementations
- FileUploadResult: standard result type for file uploads
- File operation exceptions
"""

from .config import ConfigProvider
from .core import ChatBackend
from .file_operations import (
    FileUploadHandler,
    FileUploadResult,
    FileOperationError,
    UnsupportedAdapterError,
    FileValidationError,
)

__all__ = [
    "ConfigProvider",
    "ChatBackend",
    "FileUploadHandler",
    "FileUploadResult",
    "FileOperationError",
    "UnsupportedAdapterError",
    "FileValidationError",
]

