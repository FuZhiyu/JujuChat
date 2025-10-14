"""Configuration management for RCS Adapter (YAML-based)."""

from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, field_validator


class Settings(BaseModel):
    """Application settings loaded from a YAML file."""

    # Twilio credentials and configuration
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_messaging_service_sid: Optional[str] = None
    twilio_from_number: Optional[str] = None
    twilio_webhook_secret_path: str

    # JujuChat HTTP server configuration
    claude_http_url: str = "http://127.0.0.1:8811"

    # Security settings
    public_hostname: Optional[str] = None
    adapter_max_body_bytes: int = 25_000_000  # 25MB
    adapter_rate_limit_rps: float = 2.0

    # File storage
    attachments_dir: Path = Path("./attachments")

    # Cache settings
    dedup_cache_size: int = 1024
    dedup_cache_ttl_minutes: int = 30

    # Logging
    log_level: str = "INFO"

    @field_validator("attachments_dir", mode="before")
    @classmethod
    def _coerce_path(cls, v):
        return Path(v) if not isinstance(v, Path) else v

    def model_post_init(self, __context) -> None:
        """Validate configuration after loading."""
        if not self.twilio_messaging_service_sid and not self.twilio_from_number:
            raise ValueError(
                "Either twilio_messaging_service_sid or twilio_from_number must be set"
            )

        # Ensure attachments directory exists
        self.attachments_dir.mkdir(parents=True, exist_ok=True)


def load_settings(config_path: Path) -> Settings:
    """Load Settings from a YAML file."""
    if not config_path:
        raise ValueError("Config path is required")
    p = Path(config_path)
    if not p.exists():
        raise ValueError(f"Config file not found: {p}")
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        # Allow top-level 'rcs' key or flat structure
        if isinstance(data.get("rcs"), dict):
            data = data["rcs"]
        return Settings(**data)
    except Exception as e:
        raise ValueError(f"Failed to load config from {p}: {e}")


class TwilioRequest(BaseModel):
    """Twilio webhook request parameters."""
    
    # Core fields
    MessageSid: str
    From: str
    To: str
    Body: str = ""
    
    # Media fields
    NumMedia: int = 0
    MediaUrl0: Optional[str] = None
    MediaContentType0: Optional[str] = None
    MediaUrl1: Optional[str] = None
    MediaContentType1: Optional[str] = None
    MediaUrl2: Optional[str] = None
    MediaContentType2: Optional[str] = None
    MediaUrl3: Optional[str] = None
    MediaContentType3: Optional[str] = None
    MediaUrl4: Optional[str] = None
    MediaContentType4: Optional[str] = None
    MediaUrl5: Optional[str] = None
    MediaContentType5: Optional[str] = None
    MediaUrl6: Optional[str] = None
    MediaContentType6: Optional[str] = None
    MediaUrl7: Optional[str] = None
    MediaContentType7: Optional[str] = None
    MediaUrl8: Optional[str] = None
    MediaContentType8: Optional[str] = None
    MediaUrl9: Optional[str] = None
    MediaContentType9: Optional[str] = None
    
    @property
    def media_items(self) -> list[tuple[str, str]]:
        """Get list of (url, content_type) tuples for attached media."""
        items = []
        for i in range(self.NumMedia):
            url = getattr(self, f"MediaUrl{i}", None)
            content_type = getattr(self, f"MediaContentType{i}", None)
            if url and content_type:
                items.append((url, content_type))
        return items


class ClaudeRequest(BaseModel):
    """Request format for Claude backend."""
    
    message: str
    session_id: str
    attachment_paths: Optional[list[str]] = None


class ClaudeResponse(BaseModel):
    """Response format from Claude backend."""
    
    response: str
    session_id: str
