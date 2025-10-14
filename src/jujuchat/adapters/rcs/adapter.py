"""Main FastAPI adapter for Twilio RCS webhooks."""

import logging
import time
import traceback
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any

import httpx
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import PlainTextResponse
from twilio.rest import Client as TwilioClient
from starlette.middleware.base import BaseHTTPMiddleware

from .config import Settings, TwilioRequest, ClaudeRequest, ClaudeResponse, load_settings
from .media_handler import MediaHandler
from .twilio_validator import TwilioSignatureValidator
from ...core.logging import get_adapter_logger, create_session_id

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple rate limiting middleware."""
    
    def __init__(self, app, calls_per_second: float = 2.0):
        super().__init__(app)
        self.calls_per_second = calls_per_second
        self.min_interval = 1.0 / calls_per_second
        self.last_call_time: Dict[str, float] = {}
    
    async def dispatch(self, request: Request, call_next):
        # Get client IP (simplified)
        client_ip = request.client.host if request.client else "unknown"
        
        current_time = time.time()
        last_time = self.last_call_time.get(client_ip, 0)
        
        if current_time - last_time < self.min_interval:
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
        
        self.last_call_time[client_ip] = current_time
        response = await call_next(request)
        return response


class RCSAdapter:
    """Main RCS adapter application."""
    
    def __init__(self, settings: Settings):
        """Initialize the adapter with settings."""
        self.settings = settings
        self.app = FastAPI(
            title="RCS Adapter",
            description="Twilio RCS to Claude Backend Adapter",
            version="0.1.0"
        )
        
        # Initialize components
        self.validator = TwilioSignatureValidator(settings)
        self.media_handler = MediaHandler(settings)
        self.twilio_client = TwilioClient(
            settings.twilio_account_sid,
            settings.twilio_auth_token
        )
        
        # Message deduplication cache (MessageSid -> timestamp)
        cache_ttl_seconds = settings.dedup_cache_ttl_minutes * 60
        self.message_cache = TTLCache(
            maxsize=settings.dedup_cache_size,
            ttl=cache_ttl_seconds
        )
        
        # HTTP client for Claude backend (increased timeout for first-run slowness)
        self.claude_client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        
        # Initialize logging
        self.logger = get_adapter_logger("rcs")
        
        # Add middleware
        self.app.add_middleware(
            RateLimitMiddleware,
            calls_per_second=settings.adapter_rate_limit_rps
        )
        
        # Register routes
        self._register_routes()

    async def _upload_attachments_to_core(self, session_id: str, local_paths: list[str]) -> list[str]:
        """Upload local files to core /attachments endpoint and return canonical paths.

        Falls back to original local path if upload fails (logged), but prefers
        server-managed paths so Claude can access them under history_dir.
        """
        uploaded_paths: list[str] = []
        for p in local_paths or []:
            try:
                with open(p, 'rb') as f:
                    files = {"file": (Path(p).name, f)}
                    data = {"session_id": session_id}
                    resp = await self.claude_client.post(
                        f"{self.settings.claude_http_url}/attachments",
                        files=files,
                        data=data,
                        timeout=60
                    )
                if resp.status_code == 200:
                    j = resp.json()
                    path = j.get('path') or j.get('filename')
                    if path:
                        uploaded_paths.append(path)
                        continue
                logger.warning("Attachment upload failed (%s): %s", p, resp.text[:200])
                uploaded_paths.append(p)
            except Exception as e:
                logger.warning("Attachment upload error (%s): %s", p, e)
                uploaded_paths.append(p)
        return uploaded_paths
    
    def _register_routes(self):
        """Register FastAPI routes."""
        
        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {"status": "healthy", "service": "rcs-adapter"}
        
        @self.app.get("/debug/test-claude")
        async def test_claude():
            """Test Claude backend connection - minimal info for security."""
            try:
                response = await self.claude_client.post(
                    f"{self.settings.claude_http_url}/chat",
                    json={"message": "test debug", "session_id": "rcs_debug"},
                    timeout=30
                )
                return {"status": response.status_code, "success": True}
            except Exception as e:
                logger.error("Debug endpoint error: %s", str(e))
                return {"error": "Connection failed", "success": False}
        
        @self.app.post("/twilio/rcs/{secret}", response_class=PlainTextResponse)
        async def handle_rcs_webhook(
            secret: str,
            request: Request,
            background_tasks: BackgroundTasks
        ):
            """Handle incoming Twilio RCS webhook."""
            return await self._process_webhook(secret, request, background_tasks)
    
    async def _process_webhook(
        self,
        secret: str,
        request: Request,
        background_tasks: BackgroundTasks
    ) -> str:
        """Process the Twilio webhook request."""
        try:
            # 1. Validate secret path
            if secret != self.settings.twilio_webhook_secret_path:
                logger.warning("Invalid secret path attempted")
                raise HTTPException(status_code=404, detail="Not found")
            
            # 2. Validate Host header if configured
            if self.settings.public_hostname:
                host = request.headers.get("host", "").lower()
                expected = self.settings.public_hostname.lower()
                logger.info("DEBUG: Host header received: '%s', expected: '%s'", host, expected)
                
                # Strip port number if present (e.g., "rcs.juliefu.me:443" -> "rcs.juliefu.me")
                host_without_port = host.split(':')[0]
                
                if host_without_port != expected:
                    logger.warning("Invalid Host header: '%s' (without port: '%s'), expected: '%s'", 
                                 host, host_without_port, expected)
                    raise HTTPException(status_code=403, detail="Forbidden")
            
            # 3. Get request body and validate signature
            body = await request.body()
            if len(body) > self.settings.adapter_max_body_bytes:
                raise HTTPException(status_code=413, detail="Request too large")
            
            # Parse form data
            form_data = await request.form()
            form_dict = dict(form_data)
            
            # Validate Twilio signature
            signature = request.headers.get("x-twilio-signature", "")
            if not signature:
                logger.warning("Missing X-Twilio-Signature header")
                raise HTTPException(status_code=403, detail="Missing signature")
            
            # Build the URL that Twilio used for signing
            base_url = f"https://{self.settings.public_hostname}" if self.settings.public_hostname else str(request.url.replace(query=None))
            validation_url = self.validator.build_validation_url(base_url.split('?')[0], secret)
            
            if not self.validator.validate_request(validation_url, form_dict, signature):
                raise HTTPException(status_code=403, detail="Invalid signature")
            
            # 4. Parse Twilio request
            try:
                twilio_req = TwilioRequest(**form_dict)
            except Exception as e:
                logger.error("Error parsing Twilio request: %s", str(e))
                raise HTTPException(status_code=400, detail="Invalid request format")
            
            # 5. Check for duplicate message
            if twilio_req.MessageSid in self.message_cache:
                logger.info("Duplicate message ignored: %s", twilio_req.MessageSid)
                return ""  # Return empty to acknowledge without sending message
            
            # Mark message as processed
            self.message_cache[twilio_req.MessageSid] = time.time()
            
            # 6. Process in background to return 200 quickly
            background_tasks.add_task(
                self._process_message_async,
                twilio_req
            )
            
            return ""  # Return empty to acknowledge without sending message
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Unexpected error in webhook handler: %s", str(e))
            raise HTTPException(status_code=500, detail="Internal server error")
    
    async def _process_message_async(self, twilio_req: TwilioRequest):
        """Process the message asynchronously."""
        try:
            logger.info("Starting to process message %s", twilio_req.MessageSid)
            logger.info("Message from: %s, body: %s", twilio_req.From, twilio_req.Body)
            
            # Build session ID using new logging standard
            session_id = create_session_id("rcs", twilio_req.From)
            logger.info("Session ID: %s", session_id)
            
            # Log the webhook event
            await self.logger.log_event(
                "webhook_received",
                {
                    "message_sid": twilio_req.MessageSid,
                    "from": twilio_req.From,
                    "body_length": len(twilio_req.Body),
                    "num_media": twilio_req.NumMedia,
                    "session_id": session_id
                }
            )
            
            # Download media attachments
            attachment_paths = []
            if twilio_req.NumMedia > 0:
                logger.info("Processing %d media attachments", twilio_req.NumMedia)
                attachment_paths = await self.media_handler.download_media_attachments(
                    session_id, twilio_req
                )
                logger.info("Downloaded attachments: %s", attachment_paths)
                if attachment_paths:
                    attachment_paths = await self._upload_attachments_to_core(session_id, attachment_paths)
                    logger.info("Core-managed attachment paths: %s", attachment_paths)
            
            # Skip empty messages with no attachments
            if not twilio_req.Body.strip() and not attachment_paths:
                logger.info("Skipping empty message with no attachments for %s", twilio_req.MessageSid)
                return
            
            # Build request for Claude backend
            claude_req = ClaudeRequest(
                message=twilio_req.Body,
                session_id=session_id,
                attachment_paths=attachment_paths if attachment_paths else None
            )
            logger.info("Built Claude request: %s", claude_req.model_dump())
            
            # Send to Claude backend
            logger.info("Calling Claude backend at %s", self.settings.claude_http_url)
            
            # Log Claude request
            await self.logger.log_operation(
                "claude_request",
                {
                    "session_id": session_id,
                    "message_length": len(claude_req.message),
                    "has_attachments": bool(claude_req.attachment_paths),
                    "claude_url": self.settings.claude_http_url
                }
            )
            
            response = await self.claude_client.post(
                f"{self.settings.claude_http_url}/chat",
                json=claude_req.model_dump(),
                headers={"Content-Type": "application/json"}
            )
            logger.info("Claude response status: %s", response.status_code)
            logger.info("Claude response body: %s", response.text[:500])
            
            response.raise_for_status()
            
            claude_resp = ClaudeResponse(**response.json())
            logger.info("Parsed Claude response length: %d chars", len(claude_resp.response))
            logger.info("Claude response preview: %s", claude_resp.response[:100])
            
            # Log Claude response
            await self.logger.log_operation(
                "claude_response",
                {
                    "session_id": session_id,
                    "response_length": len(claude_resp.response),
                    "status_code": response.status_code
                }
            )
            
            # Send reply via Twilio
            logger.info("Sending Twilio reply to %s", twilio_req.From)
            await self._send_twilio_reply(
                to=twilio_req.From,
                body=claude_resp.response,
                message_sid=twilio_req.MessageSid
            )
            
            # Log successful completion
            await self.logger.log_operation(
                "message_completed",
                {
                    "session_id": session_id,
                    "message_sid": twilio_req.MessageSid,
                    "response_sent": True
                }
            )
            
            logger.info("Message processing completed successfully for %s", twilio_req.MessageSid)
            
        except Exception as e:
            # Log error using new logging system
            await self.logger.log_operation(
                "processing_error",
                {
                    "session_id": session_id if 'session_id' in locals() else "unknown",
                    "message_sid": twilio_req.MessageSid,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": traceback.format_exc()
                },
                level="ERROR"
            )
            
            logger.error("Error processing message %s: %s\nType: %s\nTraceback: %s", 
                        twilio_req.MessageSid, str(e), type(e).__name__, 
                        traceback.format_exc())
            # Could implement fallback message sending here
    
    async def _send_twilio_reply(self, to: str, body: str, message_sid: str):
        """Send reply via Twilio API."""
        try:
            logger.info("Attempting to send reply: to=%s, body_length=%d", to, len(body))
            logger.info("Reply body preview: %s", body[:100])
            
            # Determine sender
            from_param = {}
            if self.settings.twilio_messaging_service_sid:
                from_param["messaging_service_sid"] = self.settings.twilio_messaging_service_sid
                logger.info("Using messaging service: %s", self.settings.twilio_messaging_service_sid)
            elif self.settings.twilio_from_number:
                from_param["from_"] = self.settings.twilio_from_number
                logger.info("Using from number: %s", self.settings.twilio_from_number)
            else:
                logger.error("No sender configured (messaging service or from number)")
                return
            
            # Run synchronous Twilio client in executor to avoid async/sync issues
            loop = asyncio.get_event_loop()
            message = await loop.run_in_executor(
                None,
                lambda: self.twilio_client.messages.create(
                    to=to,
                    body=body,
                    **from_param
                )
            )
            
            logger.info(
                "Sent Twilio reply %s in response to %s",
                message.sid, message_sid
            )
            
        except Exception as e:
            logger.error("Error sending Twilio reply: %s\nType: %s\nTraceback: %s", 
                        str(e), type(e).__name__, traceback.format_exc())
    
    async def startup(self):
        """Application startup tasks."""
        logger.info("Starting RCS Adapter")
        logger.info("Claude backend URL: %s", self.settings.claude_http_url)
        logger.info("Attachments directory: %s", self.settings.attachments_dir.absolute())
    
    async def shutdown(self):
        """Application shutdown tasks."""
        logger.info("Shutting down RCS Adapter")
        await self.claude_client.aclose()
        await self.media_handler.cleanup()


def create_app(settings: Settings = None) -> FastAPI:
    """Factory function to create the FastAPI app."""
    if settings is None:
        # Attempt to load default YAML config from CWD
        default_cfg = Path("rcs_config.yaml")
        if not default_cfg.exists():
            raise RuntimeError("No settings provided and rcs_config.yaml not found in current directory")
        settings = load_settings(default_cfg)
    
    adapter = RCSAdapter(settings)
    
    @adapter.app.on_event("startup")
    async def startup_event():
        await adapter.startup()
    
    @adapter.app.on_event("shutdown")
    async def shutdown_event():
        await adapter.shutdown()
    
    return adapter.app
