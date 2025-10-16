from __future__ import annotations

from pathlib import Path
from typing import Optional

from .attachments import get_session_attachments_dir
from .config import get_config


def _validate_path_under(base: Path, path: Path) -> Path:
    """Ensure the given path exists, is a file, and is under base.

    Returns the resolved path. Raises ValueError otherwise.
    """
    p = path.expanduser().resolve()
    base = base.resolve()
    if not str(p).startswith(str(base)):
        raise ValueError(f"Path not allowed: {p}")
    if not p.exists() or not p.is_file():
        raise ValueError(f"File not found: {p}")
    return p


def _resolve_file_path(base: Path, session_id: str, file_path: str) -> Path:
    """Resolve a path to an existing file without creating copies.

    Resolution for relative paths (Slack):
    - Only resolved against the channel's Claude root (`claude_initial_path`).
    For absolute paths: return if exists.

    Raises ValueError if not found.
    """
    base = base.resolve()
    src = Path(file_path)

    if src.is_absolute():
        p = src.expanduser().resolve()
        if p.exists() and p.is_file():
            return p
        raise ValueError(f"File not found: {p}")

    # Resolve relative to session's Claude initial path from Slack config (channel-specific)
    try:
        # session_id format: 'slack_<channel>'
        parts = session_id.split('_', 1)
        channel_id = parts[1] if len(parts) == 2 else session_id
        cfg = get_config()
        app_cfg = cfg.get_channel_config(channel_id)
        initial_path = getattr(app_cfg, 'claude_initial_path', None)
        if initial_path:
            init_candidate = (Path(initial_path) / src).expanduser().resolve()
            if init_candidate.exists() and init_candidate.is_file():
                return init_candidate
    except Exception:
        # Best-effort; ignore config lookup failures
        pass

    raise ValueError(f"File not found: {src}")


# Note: No destination copying is performed; uploads stream the source file.


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
    p = _resolve_file_path(base_dir, session_id, file_path)

    # Slack recommends files_upload_v2
    # Use a normal (sync) file handle inside async function
    size = p.stat().st_size
    if size <= 1:
        raise ValueError(f"File too small to upload (must be > 1 byte): {p}")

    with p.open('rb') as f:
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
