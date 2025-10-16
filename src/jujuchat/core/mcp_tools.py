"""MCP tools for ChatBackend - exposed to agents via Agent SDK.

These tools are made available to Claude agents through an MCP server
to perform operations like file uploads that interact with the chat platform.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Dict, Any

from claude_agent_sdk import tool, create_sdk_mcp_server

if TYPE_CHECKING:
    from .core import ChatBackend

logger = logging.getLogger(__name__)


def create_file_operations_mcp_server(backend: "ChatBackend", session_id: str):
    """Create an MCP server with file operation tools for agents.

    This creates a session-specific MCP server that provides file upload
    capabilities to the agent.

    Args:
        backend: ChatBackend instance that handles operations
        session_id: Session ID for routing operations

    Returns:
        MCP server instance with file operation tools
    """

    # Capture session_id in closure for the tool function
    _captured_session_id = session_id

    @tool(
        "upload_file",
        """Upload a file to the chat platform (Slack, RCS, etc.).

Use this tool to share files with the user after creating them or when the user requests an existing file.

PATH RESOLUTION:
- Absolute paths: "/full/path/to/file.pdf" - recommended, used as-is
- Relative paths: "report.pdf" - resolved against current working directory

USAGE:
1. Create or locate the file you want to upload
2. Call this tool with the file path
3. Check the response before confirming to the user

EXAMPLES:

Create and upload:
  upload_file(file_path="/absolute/path/to/chart.png", title="Sales Chart")

Upload existing file:
  upload_file(file_path="report.pdf", comment="Here's the report")

Upload to thread (Slack):
  upload_file(file_path="/path/to/file.md", thread_ts="1234567890.123456")

Note: Platform-specific options like thread_ts can be passed as additional parameters.""",
        {
            "file_path": {
                "type": "string",
                "description": "Path to the file to upload. Absolute path recommended (e.g., '/full/path/to/file.pdf'). Relative paths (e.g., 'report.pdf') resolve against current working directory."
            },
            "title": {
                "type": "string",
                "description": "Optional title/caption for the file. Examples: 'Monthly Report', 'Sales Chart'"
            },
            "comment": {
                "type": "string",
                "description": "Optional message to include with upload. Examples: 'Here is the report!', 'Analysis complete'"
            }
        }
    )
    async def upload_file_tool(args: Dict[str, Any]) -> Dict[str, Any]:
        """Upload a file to the chat platform."""
        file_path = args.get("file_path")
        title = args.get("title")
        comment = args.get("comment")

        if not file_path:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: file_path is required"
                }],
                "is_error": True
            }

        # Extract any additional platform-specific kwargs
        kwargs = {k: v for k, v in args.items() if k not in ["file_path", "title", "comment"]}

        try:
            logger.info(
                "Agent invoking upload_file tool",
                extra={
                    "session_id": _captured_session_id,
                    "file_path": file_path,
                    "title": title
                }
            )

            result = await backend.upload_file(
                session_id=_captured_session_id,
                file_path=file_path,
                title=title,
                comment=comment,
                **kwargs
            )

            if result.success:
                # Build success message
                message_parts = [f"✅ File uploaded successfully: {file_path}"]

                if result.message:
                    message_parts.append(f"\n{result.message}")

                # Include platform-specific info if available
                if result.platform_data:
                    file_url = result.platform_data.get("file_url")
                    file_id = result.platform_data.get("file_id")

                    if file_url:
                        message_parts.append(f"\nURL: {file_url}")
                    if file_id:
                        message_parts.append(f"\nFile ID: {file_id}")

                return {
                    "content": [{
                        "type": "text",
                        "text": "\n".join(message_parts)
                    }]
                }
            else:
                # Upload failed
                error_msg = result.error or "Upload failed for unknown reason"
                logger.warning(
                    "Upload tool failed",
                    extra={
                        "session_id": _captured_session_id,
                        "file_path": file_path,
                        "error": error_msg
                    }
                )
                return {
                    "content": [{
                        "type": "text",
                        "text": f"❌ Upload failed: {error_msg}"
                    }],
                    "is_error": True
                }

        except Exception as e:
            logger.error(
                "Upload tool exception",
                extra={
                    "session_id": _captured_session_id,
                    "file_path": file_path,
                    "error": str(e),
                },
                exc_info=True
            )
            return {
                "content": [{
                    "type": "text",
                    "text": f"❌ Upload error: {str(e)}"
                }],
                "is_error": True
            }

    # Create the MCP server with the upload tool
    server = create_sdk_mcp_server(
        name="jujuchat-file-ops",
        version="1.0.0",
        tools=[upload_file_tool]
    )

    logger.info(
        "Created file operations MCP server",
        extra={
            "session_id": _captured_session_id,
            "tools": ["upload_file"]
        }
    )

    return server
