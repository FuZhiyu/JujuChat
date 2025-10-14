from __future__ import annotations

from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Request model for chat endpoint - matches iOS app expectations."""
    message: str
    session_id: Optional[str] = "default"
    attachment_paths: Optional[List[str]] = None


class ChatResponse(BaseModel):
    """Response model for chat endpoint - matches iOS app expectations."""
    response: str
    session_id: str


class HealthResponse(BaseModel):
    """Response model for health check endpoint."""
    status: str
    service: str
    working_directory: Optional[str] = None


class AttachmentUploadResponse(BaseModel):
    attachment_id: str
    path: str
    filename: str
    size: int
    mime: Optional[str] = None


class SessionSummary(BaseModel):
    session_id: str
    last_activity_at: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class HistoryEvent(BaseModel):
    timestamp: str
    session_id: str
    type: str
    text: Optional[str] = None
    attachment_paths: Optional[List[str]] = None
    detail: Optional[Dict[str, Any]] = None
