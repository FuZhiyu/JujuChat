from __future__ import annotations

import os
import asyncio
from pathlib import Path
from typing import Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from cachetools import TTLCache

from .core import ChatBackend
from .config_providers import IOSConfigProvider
from .models import ChatRequest, ChatResponse, HealthResponse, AttachmentUploadResponse, HistoryEvent
from .history import ChatHistoryManager


class HTTPServer:
    """FastAPI HTTP server for Claude backend."""
    
    def __init__(self, config_path: Optional[Path] = None):
        """Initialize HTTP server with configuration."""
        self.config_path = config_path or Path("ios_config.yaml")
        self.config_provider = IOSConfigProvider(self.config_path)
        self.claude_backend = ChatBackend(self.config_provider)
        self.history = ChatHistoryManager(self.config_provider.get_session_config("_bootstrap").history_dir)  # bootstrap for base path
        
        # Cache for attachment-first messages (session_id -> attachment_data)
        self.pending_attachments = TTLCache(maxsize=100, ttl=60)  # 60 seconds TTL
        
        self.app = self._create_app()

        # Optional stub mode for tests: bypass Claude CLI and echo
        if os.environ.get("CLAUDE_BACKEND_STUB"):
            class _StubBackend:
                async def send_message_with_session(self, message: str, session_id: str) -> str:
                    return f"OK:{session_id}:{message}"
                async def cleanup_all_sessions(self) -> None:
                    return None
            self.claude_backend = _StubBackend()
    
    def _create_app(self) -> FastAPI:
        """Create and configure FastAPI application."""
        app = FastAPI(
            title="Claude Backend HTTP Server",
            version="1.0.0",
            description="HTTP API wrapper for Claude backend"
        )
        
        # Add CORS middleware for iOS and web clients
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        # Register routes
        self._register_routes(app)
        
        return app
    
    def _is_audio_file(self, path: str) -> bool:
        """Check if a file path represents an audio file."""
        audio_extensions = {'.mp3', '.wav', '.m4a', '.ogg', '.aac', '.flac', '.opus', '.amr', '.3gp'}
        return Path(path).suffix.lower() in audio_extensions
    
    def _register_routes(self, app: FastAPI) -> None:
        """Register API routes."""
        
        @app.post("/chat", response_model=ChatResponse)
        async def chat_endpoint(request: ChatRequest) -> ChatResponse:
            """Send message to Claude and get response."""
            try:
                # Prefix iOS sessions to avoid collision with other frontends
                session_id = f"ios_{request.session_id}"

                # Check for cached attachments from previous message
                attachment_paths = request.attachment_paths or []
                if session_id in self.pending_attachments:
                    cached = self.pending_attachments.pop(session_id)
                    attachment_paths = attachment_paths + cached['paths']
                    print(f"Combined current attachments with {len(cached['paths'])} cached attachments")

                # Handle empty messages (might have had attachments that failed to download)
                message_text = (request.message or "").strip()
                
                # If message is completely empty, provide a default
                if not message_text and not attachment_paths:
                    message_text = "User sent a message without text content."
                    print(f"Empty message detected, using default text: {message_text}")
                elif not message_text and attachment_paths:
                    # Check if any attachment is audio
                    audio_files = [path for path in attachment_paths if self._is_audio_file(path)]
                    if audio_files:
                        # Generate transcription prompt for audio
                        audio_list = ", ".join(audio_files)
                        message_text = f"User sent audio message(s) at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}. Files: {audio_list}. Use whisper_transcribe tool to transcribe and respond."
                        print(f"Generated transcription prompt for audio files: {audio_list}")
                    else:
                        # Cache non-audio attachments and wait for text message
                        self.pending_attachments[session_id] = {
                            'paths': attachment_paths,
                            'timestamp': datetime.now()
                        }
                        print(f"Cached {len(attachment_paths)} attachment(s), waiting for text message")
                        return ChatResponse(
                            response="Attachment received, waiting for your message...",
                            session_id=request.session_id
                        )

                # Validate paths under session history dir
                valid_paths = self.history.validate_paths(session_id, attachment_paths) if attachment_paths else []
                await self.history.record_user(session_id, message_text, valid_paths)

                # Prepare attachment preface
                preface = ""
                if valid_paths:
                    joined = ", ".join(valid_paths)
                    preface = f"Attachments available on disk (readable via file tools): {joined}\n\n"

                # Final safety check: ensure message is not empty after all processing
                final_message = (preface + message_text) if preface else message_text
                if not final_message or not final_message.strip():
                    final_message = "User sent an empty message."
                    print(f"Empty message detected at final stage, using default: {final_message}")

                response = await self.claude_backend.send_message_with_session(
                    message=final_message,
                    session_id=session_id
                )
                
                await self.history.record_assistant(session_id, response)
                return ChatResponse(
                    response=response,
                    session_id=request.session_id
                )
                
            except Exception as e:
                import traceback
                error_details = f"Claude backend error: {str(e)}\nTraceback: {traceback.format_exc()}"
                print(f"Chat endpoint error: {error_details}")
                raise HTTPException(
                    status_code=500, 
                    detail=f"Claude backend error: {str(e)}"
                )
        
        @app.get("/health", response_model=HealthResponse)
        async def health_endpoint() -> HealthResponse:
            """Health check endpoint."""
            return HealthResponse(
                status="healthy",
                service="Claude Backend HTTP Server",
                working_directory=str(Path.cwd())
            )

        @app.post("/attachments", response_model=AttachmentUploadResponse)
        async def upload_attachment(file: UploadFile = File(...), session_id: str = Form("default")) -> AttachmentUploadResponse:
            """Upload an attachment to the session's attachments folder."""
            try:
                sid = f"ios_{session_id}"
                cfg = self.config_provider.get_session_config(sid)
                max_mb = cfg.attachments_max_size_mb or 25
                max_bytes = max_mb * 1024 * 1024
                mime = file.content_type

                meta = await self.history.save_upload(sid, file.filename, file, max_bytes, mime)
                return AttachmentUploadResponse(
                    attachment_id=meta.id,
                    path=meta.path,
                    filename=meta.filename,
                    size=meta.size,
                    mime=meta.mime,
                )
            except ValueError as ve:
                raise HTTPException(status_code=413, detail=str(ve))
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

        @app.get("/sessions/{session_id}/history")
        async def get_history(session_id: str, limit: int = 50) -> list[HistoryEvent]:
            sid = f"ios_{session_id}"
            events = await self.history.load_history(sid, limit=limit)
            # Pydantic will coerce
            return events

        @app.get("/sessions/{session_id}/attachments")
        async def get_attachments(session_id: str):
            sid = f"ios_{session_id}"
            return {"attachments": self.history.list_attachments(sid)}
        
        @app.get("/sessions")
        async def list_sessions():
            """List active Claude sessions."""
            return {
                "active_sessions": self.claude_backend.get_active_sessions()
            }
        
        @app.post("/sessions/{session_id}/reset")
        async def reset_session(session_id: str):
            """Reset a specific session."""
            try:
                # Use same iOS prefix as chat endpoint
                full_session_id = f"ios_{session_id}"
                await self.claude_backend.reset_session(full_session_id)
                return {"status": "session_reset", "session_id": session_id}
            except Exception as e:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to reset session: {str(e)}"
                )
    
    async def startup(self):
        """Startup tasks."""
        print(f"Starting Claude Backend HTTP Server")
        print(f"Configuration file: {self.config_path.resolve()}")
        print(f"Working directory: {Path.cwd()}")
    
    async def shutdown(self):
        """Cleanup tasks."""
        print("Shutting down Claude Backend HTTP Server...")
        await self.claude_backend.cleanup_all_sessions()
        print("All Claude sessions cleaned up")


def create_app(config_path: Optional[Path] = None) -> FastAPI:
    """Factory function to create FastAPI application."""
    server = HTTPServer(config_path)
    
    @server.app.on_event("startup")
    async def startup_event():
        await server.startup()
    
    @server.app.on_event("shutdown") 
    async def shutdown_event():
        await server.shutdown()
    
    return server.app
