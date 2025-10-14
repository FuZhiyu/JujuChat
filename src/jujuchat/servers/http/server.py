"""
Unified HTTP server for JujuChat.

Provides HTTP API endpoints for:
- RCS webhook handling (Twilio)
- Generic chat API (future iOS/web clients)
- Health and status endpoints
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any

from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
import uvicorn

from ...core.http_server import HTTPServer as CoreHTTPServer
from ...core.models import ChatRequest, ChatResponse, HealthResponse

logger = logging.getLogger(__name__)


class WebhookRequest(BaseModel):
    """Generic webhook request model."""
    data: Dict[str, Any]
    headers: Dict[str, str] = {}


class JujuChatHTTPServer:
    """Unified HTTP server for all JujuChat endpoints."""
    
    def __init__(self, config_path: Optional[Path] = None, host: str = "127.0.0.1", port: int = 8811):
        """Initialize the server."""
        # Resolve config path eagerly to avoid CWD confusion under launchd
        self.config_path = (config_path.resolve() if config_path else Path("server_config.yaml").resolve())
        self.host = host
        self.port = port
        
        # Initialize core backend server for chat functionality
        self.core_server = CoreHTTPServer(self.config_path)

        # Use the core app as the primary application (consolidation: single API surface)
        self.app = self.core_server.app

        # Add adapter-specific endpoints under /rcs/* to avoid route collisions
        self._register_rcs_routes(self.app)
        
    def _register_rcs_routes(self, app: FastAPI):
        """Register adapter-specific RCS webhook endpoints under /rcs."""

        @app.post("/rcs/twilio/{webhook_path}")
        async def rcs_webhook(webhook_path: str, request: Request, background_tasks: BackgroundTasks):
            """
            RCS webhook endpoint for Twilio.
            
            This endpoint will be used by the RCS adapter to process incoming
            RCS messages. For now, it's a placeholder that logs the webhook data.
            """
            try:
                # Get raw body for signature validation
                body = await request.body()
                headers = dict(request.headers)
                
                logger.info(f"Received RCS webhook on path: {webhook_path}")
                logger.debug(f"Headers: {headers}")
                logger.debug(f"Body length: {len(body)}")
                
                # For now, just acknowledge receipt
                # The actual RCS adapter will handle the webhook processing
                return PlainTextResponse("OK", status_code=200)
                
            except Exception as e:
                logger.error(f"RCS webhook error: {e}")
                raise HTTPException(status_code=500, detail="Webhook processing failed")
    
    async def start(self):
        """Start the server."""
        logger.info(f"Starting JujuChat HTTP server on {self.host}:{self.port}")
        config = uvicorn.Config(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="info"
        )
        server = uvicorn.Server(config)
        await server.serve()
    
    def run(self):
        """Run the server (blocking)."""
        uvicorn.run(
            app=self.app,
            host=self.host,
            port=self.port,
            log_level="info"
        )


async def main():
    """Main entry point for the server."""
    import argparse
    
    parser = argparse.ArgumentParser(description="JujuChat HTTP Server")
    parser.add_argument("--config", type=Path, help="Configuration file path")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8811, help="Port to bind to")
    
    args = parser.parse_args()
    
    # Set up logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Create and start server
    server = JujuChatHTTPServer(
        config_path=args.config,
        host=args.host,
        port=args.port
    )
    
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
