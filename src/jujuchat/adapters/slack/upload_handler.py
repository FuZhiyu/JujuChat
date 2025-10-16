"""Slack-specific file upload handler implementation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from slack_sdk.web.async_client import AsyncWebClient

from ...core.file_operations import FileUploadResult, FileValidationError
from .sender import upload_local_file

logger = logging.getLogger(__name__)


class SlackUploadHandler:
    """File upload handler for Slack adapter.

    Implements the FileUploadHandler protocol for uploading files to Slack.
    Uses the existing upload_local_file function with security validation.
    """

    def __init__(self, client: AsyncWebClient, bot_token: str):
        """Initialize Slack upload handler.

        Args:
            client: Async Slack client for API calls
            bot_token: Bot token for authentication
        """
        self.client = client
        self.bot_token = bot_token

    async def upload_file(
        self,
        session_id: str,
        file_path: str,
        *,
        title: Optional[str] = None,
        comment: Optional[str] = None,
        thread_ts: Optional[str] = None,
        **kwargs
    ) -> FileUploadResult:
        """Upload a file to Slack.

        Args:
            session_id: Session ID in format 'slack_CHANNEL_ID'
            file_path: Path to file within session's attachments directory
            title: Optional title for the file
            comment: Optional comment/message with the upload
            thread_ts: Optional thread timestamp for threaded upload
            **kwargs: Additional Slack-specific options

        Returns:
            FileUploadResult with upload status

        Raises:
            FileValidationError: If file validation fails
            RuntimeError: If Slack API call fails
        """
        # Extract channel ID from session_id (format: slack_CHANNEL_ID)
        parts = session_id.split("_", 1)
        if len(parts) != 2:
            raise ValueError(
                f"Invalid session_id format: '{session_id}'. "
                "Expected 'slack_CHANNEL_ID'"
            )
        channel_id = parts[1]

        logger.info(
            "Uploading file to Slack",
            extra={
                "session_id": session_id,
                "channel_id": channel_id,
                "file_path": file_path,
            }
        )

        try:
            # Use existing validated upload function
            response = await upload_local_file(
                self.client,
                channel=channel_id,
                session_id=session_id,
                file_path=file_path,
                title=title,
                initial_comment=comment,
                thread_ts=thread_ts,
            )

            # Check if upload was successful
            if not response.get("ok"):
                error_msg = response.get("error", "Unknown error")
                logger.error(
                    "Slack file upload failed",
                    extra={
                        "session_id": session_id,
                        "error": error_msg,
                    }
                )
                return FileUploadResult(
                    success=False,
                    file_path=file_path,
                    error=f"Slack API error: {error_msg}",
                )

            # Extract file info from response
            file_info = response.get("file", {})
            file_url = file_info.get("permalink") or file_info.get("url_private")

            logger.info(
                "File uploaded successfully to Slack",
                extra={
                    "session_id": session_id,
                    "file_id": file_info.get("id"),
                    "file_url": file_url,
                }
            )

            return FileUploadResult(
                success=True,
                file_path=file_path,
                message=f"File uploaded to Slack: {Path(file_path).name}",
                platform_data={
                    "file_id": file_info.get("id"),
                    "file_url": file_url,
                    "channel_id": channel_id,
                    "thread_ts": thread_ts,
                    "slack_response": response,
                },
            )

        except ValueError as e:
            # File validation errors
            logger.warning(
                "File validation failed",
                extra={
                    "session_id": session_id,
                    "file_path": file_path,
                    "error": str(e),
                }
            )
            raise FileValidationError(str(e)) from e

        except Exception as e:
            # Slack API or other errors
            logger.error(
                "Unexpected error during file upload",
                extra={
                    "session_id": session_id,
                    "error": str(e),
                },
                exc_info=True,
            )
            return FileUploadResult(
                success=False,
                file_path=file_path,
                error=f"Upload failed: {str(e)}",
            )
