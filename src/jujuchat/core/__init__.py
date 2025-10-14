"""
Core backend module for JujuChat.

Provides the shared Claude integration backend that all adapters use.

Exports:
- ChatBackend: core persistent backend class (renamed from ClaudeBackend)
- ConfigProvider: protocol for per-session config lookup
"""

from .config import ConfigProvider
from .core import ChatBackend

__all__ = [
    "ConfigProvider",
    "ChatBackend",
]

