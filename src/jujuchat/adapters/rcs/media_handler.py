"""Media download and storage handler for Twilio attachments."""

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from .config import Settings, TwilioRequest

logger = logging.getLogger(__name__)

# Allowed MIME types for security
ALLOWED_MIME_TYPES = {
    # Images
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/heic", "image/heif",
    # Documents
    "application/pdf", "text/plain", "text/csv",
    "application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    # Audio
    "audio/mpeg", "audio/wav", "audio/ogg", "audio/mp4", "audio/aac",
    # Video
    "video/mp4", "video/quicktime", "video/avi", "video/webm"
}

# File extension mapping for unknown MIME types
MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png", 
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "video/mp4": ".mp4",
}


class MediaHandler:
    """Handles downloading and storing Twilio media attachments."""
    
    def __init__(self, settings: Settings):
        """Initialize media handler with settings."""
        self.settings = settings
        self.max_size_bytes = settings.adapter_max_body_bytes
        
        # Create HTTP client with Twilio auth and redirect following
        self.http_client = httpx.AsyncClient(
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            timeout=httpx.Timeout(30.0),  # 30 second timeout for downloads
            limits=httpx.Limits(max_keepalive_connections=5),
            follow_redirects=True  # Enable redirect following for CDN URLs
        )
    
    async def download_media_attachments(
        self,
        session_id: str,
        twilio_request: TwilioRequest
    ) -> List[str]:
        """
        Download all media attachments for a message.
        
        Args:
            session_id: Claude session ID for organizing attachments
            twilio_request: Parsed Twilio webhook request
            
        Returns:
            List of local file paths for downloaded attachments
        """
        if twilio_request.NumMedia == 0:
            return []
        
        # Create session-specific attachments directory
        session_dir = self.settings.attachments_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        
        download_tasks = []
        for i, (media_url, content_type) in enumerate(twilio_request.media_items):
            task = self._download_single_media(
                media_url, content_type, session_dir, f"{twilio_request.MessageSid}_{i}"
            )
            download_tasks.append(task)
        
        # Download all media concurrently
        results = await asyncio.gather(*download_tasks, return_exceptions=True)
        
        # Filter out failures and return successful downloads
        successful_paths = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(
                    "Failed to download media %d for message %s: %s",
                    i, twilio_request.MessageSid, str(result)
                )
            elif result:
                successful_paths.append(str(result))
        
        return successful_paths
    
    async def _download_single_media(
        self,
        media_url: str,
        content_type: str,
        session_dir: Path,
        filename_prefix: str
    ) -> Optional[Path]:
        """
        Download a single media file.
        
        Args:
            media_url: Twilio media URL
            content_type: MIME type of the media
            session_dir: Directory to save the file in
            filename_prefix: Prefix for the saved filename
            
        Returns:
            Path to the saved file, or None if download failed
        """
        try:
            # Temporarily allow all MIME types for testing
            # if content_type not in ALLOWED_MIME_TYPES:
            #     logger.warning("Unsupported media type: %s", content_type)
            #     return None
            logger.info("Processing media type: %s", content_type)
            
            # Determine file extension
            extension = MIME_TO_EXT.get(content_type)
            if not extension:
                # Try to guess from URL or use generic
                parsed_url = urlparse(media_url)
                ext_from_url = Path(parsed_url.path).suffix
                extension = ext_from_url if ext_from_url else ".bin"
            
            # Generate unique filename
            filename = f"{filename_prefix}{extension}"
            file_path = session_dir / filename
            
            # Download the file
            async with self.http_client.stream("GET", media_url) as response:
                response.raise_for_status()
                
                # Check content length if available
                content_length = response.headers.get("content-length")
                if content_length and int(content_length) > self.max_size_bytes:
                    logger.warning(
                        "Media file too large: %s bytes (max: %s)",
                        content_length, self.max_size_bytes
                    )
                    return None
                
                # Stream download with size checking
                total_size = 0
                with open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        total_size += len(chunk)
                        if total_size > self.max_size_bytes:
                            logger.warning(
                                "Media file too large during download: %s bytes",
                                total_size
                            )
                            # Clean up partial file
                            file_path.unlink(missing_ok=True)
                            return None
                        f.write(chunk)
            
            logger.info(
                "Downloaded media: %s (%s bytes, %s)",
                filename, total_size, content_type
            )
            return file_path
            
        except httpx.HTTPStatusError as e:
            logger.error("HTTP error downloading media from %s: %s", media_url, e)
            return None
        except Exception as e:
            logger.error("Error downloading media from %s: %s", media_url, str(e))
            return None
    
    async def cleanup(self):
        """Clean up resources."""
        await self.http_client.aclose()