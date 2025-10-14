from __future__ import annotations

from pathlib import Path
from typing import Optional

from .attachments import get_session_attachments_dir


def _validate_path_under(base: Path, path: Path) -> Path:
    p = path.expanduser().resolve()
    base = base.resolve()
    if not str(p).startswith(str(base)):
        raise ValueError(f"Path not allowed: {p}")
    if not p.exists() or not p.is_file():
        raise ValueError(f"File not found: {p}")
    return p


async def upload_local_file(
    client,
    *,
    channel: str,
    session_id: str,
    file_path: str,
    title: Optional[str] = None,
    initial_comment: Optional[str] = None,
    thread_ts: Optional[str] = None,
):
    """Upload a local file to Slack within the session's attachments directory.

    Raises ValueError on validation errors; propagates Slack API errors.
    """
    base_dir = get_session_attachments_dir(session_id)
    p = _validate_path_under(base_dir, Path(file_path))

    # Slack recommends files_upload_v2
    async with p.open('rb') as f:
        args = {
            'channel': channel,
            'filename': p.name,
            'file': f,
        }
        if title:
            args['title'] = title
        if initial_comment:
            args['initial_comment'] = initial_comment
        if thread_ts:
            args['thread_ts'] = thread_ts
        return await client.files_upload_v2(**args)

