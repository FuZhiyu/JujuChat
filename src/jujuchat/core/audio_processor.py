from __future__ import annotations

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class AudioProcessor:
    """Audio processing with mlx_whisper transcription support."""
    
    def __init__(self, 
                 transcription_enabled: bool = True,
                 mlx_whisper_model: Optional[str] = None,
                 transcription_language: Optional[str] = None,
                 transcription_timeout: int = 300):
        """Initialize audio processor.
        
        Args:
            transcription_enabled: Whether transcription is enabled
            mlx_whisper_model: Model to use for transcription (e.g., "base", "small", "medium")
            transcription_language: Language code for transcription (e.g., "en", "es")
            transcription_timeout: Timeout in seconds for transcription
        """
        self.transcription_enabled = transcription_enabled
        self.mlx_whisper_model = mlx_whisper_model or "base"
        self.transcription_language = transcription_language
        self.transcription_timeout = transcription_timeout
        self._mlx_whisper_available: Optional[bool] = None
    
    async def check_mlx_whisper_available(self) -> bool:
        """Check if mlx_whisper is available in the system."""
        if self._mlx_whisper_available is not None:
            return self._mlx_whisper_available
        
        try:
            proc = await asyncio.create_subprocess_exec(
                "mlx_whisper", "--help",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=10)
            self._mlx_whisper_available = proc.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError, Exception):
            self._mlx_whisper_available = False
        
        if not self._mlx_whisper_available:
            logger.warning("mlx_whisper not available - transcription disabled")
        
        return self._mlx_whisper_available
    
    def is_transcribable_audio(self, mime_type: Optional[str]) -> bool:
        """Check if the MIME type is a transcribable audio format."""
        if not mime_type:
            return False
        
        transcribable_types = {
            "audio/aac",
            "audio/mp4",
            "audio/mpeg",
            "audio/mp3",
            "audio/wav",
            "audio/wave",
            "audio/x-wav",
            "audio/flac",
            "audio/ogg",
            "audio/webm"
        }
        
        return mime_type.lower() in transcribable_types
    
    async def transcribe_audio_file(self, audio_path: Path) -> Optional[str]:
        """Transcribe audio file using mlx_whisper.
        
        Args:
            audio_path: Path to the audio file to transcribe
            
        Returns:
            Transcription text or None if failed
        """
        if not self.transcription_enabled:
            logger.debug("Transcription disabled in configuration")
            return None
        
        if not await self.check_mlx_whisper_available():
            logger.warning("mlx_whisper not available, skipping transcription")
            return None
        
        if not audio_path.exists():
            logger.error(f"Audio file not found: {audio_path}")
            return None
        
        try:
            # Build mlx_whisper command
            cmd = ["mlx_whisper", str(audio_path)]
            
            # Add model if specified
            if self.mlx_whisper_model:
                cmd.extend(["--model", self.mlx_whisper_model])
            
            # Add language if specified
            if self.transcription_language:
                cmd.extend(["--language", self.transcription_language])
            
            # Add output format (text only)
            cmd.extend(["--output_format", "txt"])
            
            # Use a temporary directory for output
            with tempfile.TemporaryDirectory() as temp_dir:
                cmd.extend(["--output_dir", temp_dir])
                
                logger.info(f"Starting transcription: {' '.join(cmd)}")
                
                # Run transcription
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), 
                    timeout=self.transcription_timeout
                )
                
                if proc.returncode != 0:
                    error_msg = stderr.decode('utf-8', errors='ignore')
                    logger.error(f"mlx_whisper failed with return code {proc.returncode}: {error_msg}")
                    return None
                
                # Find the generated transcript file
                temp_path = Path(temp_dir)
                transcript_files = list(temp_path.glob("*.txt"))
                
                if not transcript_files:
                    logger.error("No transcript file generated")
                    return None
                
                # Read the transcript
                transcript_path = transcript_files[0]
                transcription = transcript_path.read_text(encoding='utf-8', errors='ignore').strip()
                
                if not transcription:
                    logger.warning("Empty transcription result")
                    return None
                
                logger.info(f"Transcription successful: {len(transcription)} characters")
                return transcription
                
        except asyncio.TimeoutError:
            logger.error(f"Transcription timed out after {self.transcription_timeout} seconds")
            return None
        except Exception as e:
            logger.error(f"Transcription failed: {str(e)}")
            return None
    
    async def process_audio_attachment(self, audio_path: Path, mime_type: Optional[str]) -> Dict[str, Any]:
        """Process an audio attachment and return processing results.
        
        Args:
            audio_path: Path to the audio file
            mime_type: MIME type of the audio file
            
        Returns:
            Dict with processing results including transcription if available
        """
        result = {
            "transcription": None,
            "transcription_available": False,
            "error": None
        }
        
        if not self.is_transcribable_audio(mime_type):
            result["error"] = f"MIME type {mime_type} not supported for transcription"
            return result
        
        try:
            transcription = await self.transcribe_audio_file(audio_path)
            if transcription:
                result["transcription"] = transcription
                result["transcription_available"] = True
            else:
                result["error"] = "Transcription failed"
        except Exception as e:
            result["error"] = f"Audio processing error: {str(e)}"
        
        return result