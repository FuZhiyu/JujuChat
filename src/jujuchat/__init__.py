"""
JujuChat: Unified chat integration module for the Juju system.

Consolidates Slack, RCS, and HTTP chat interfaces into a single module
with shared core backend and platform-specific adapters.
"""

__version__ = "0.1.0"

# Re-export core components for convenience
from .core import ChatBackend, ConfigProvider

__all__ = [
    "ChatBackend",
    "ConfigProvider",
]