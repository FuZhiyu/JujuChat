#!/usr/bin/env python3
"""
Claude Backend HTTP Server Entry Point

Usage:
    python -m claude_backend [--host HOST] [--port PORT] [--config CONFIG]
    
Examples:
    python -m claude_backend
    python -m claude_backend --port 8100
    python -m claude_backend --config /path/to/ios_config.yaml
"""

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

from .http_server import create_app


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Claude Backend HTTP Server",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    
    parser.add_argument(
        "--port",
        type=int,
        default=8100,
        help="Port to bind to (default: 8100)"
    )
    
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to configuration YAML file (default: ios_config.yaml)"
    )
    
    parser.add_argument(
        "--log-level",
        choices=["critical", "error", "warning", "info", "debug"],
        default="info",
        help="Log level (default: info)"
    )
    
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload for development"
    )
    
    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_args()
    
    # Create FastAPI app with optional config path
    app = create_app(args.config)
    
    print(f"Starting Claude Backend HTTP Server")
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    if args.config:
        print(f"Config: {args.config}")
    print(f"Log level: {args.log_level}")
    
    try:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level=args.log_level,
            reload=args.reload
        )
    except KeyboardInterrupt:
        print("\nShutdown requested by user")
    except Exception as e:
        print(f"Server error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()