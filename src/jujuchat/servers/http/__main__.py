"""
Entry point for JujuChat HTTP server.

Usage:
    python -m jujuchat.servers.http
    python -m jujuchat.servers.http --config server_config.yaml --port 8811
"""

import asyncio
from .server import main

if __name__ == "__main__":
    asyncio.run(main())