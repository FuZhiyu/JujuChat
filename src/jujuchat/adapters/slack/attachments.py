from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Iterable, Tuple

import aiofiles
import aiohttp

from ...core.logging import get_core_logger


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class SavedAttachment:
    path: Path
    filename: str
    size: int
    mime: Optional[str]


def _sanitize_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    name = _FILENAME_SAFE_RE.sub("", name)
    return name or "file"


def get_session_attachments_dir(session_id: str) -> Path:
    """Return the attachments directory under the core logs session path."""
    core = get_core_logger()
    # logs/jujuchat-core/{session_id}/attachments
    session_dir = core.core_log_dir / session_id
    attach_dir = session_dir / "attachments"
    attach_dir.mkdir(parents=True, exist_ok=True)
    return attach_dir


def _is_allowed_type(filename: str, mime: Optional[str], allowed: Optional[Iterable[str]]) -> bool:
    if not allowed:
        return True
    allowed_set = {str(a).strip().lower() for a in allowed}
    ext = Path(filename).suffix.lower().lstrip(".")
    m = (mime or "").lower()

    def any_prefix(prefix: str) -> bool:
        return any(m.startswith(prefix) for m in (m,))

    if "image" in allowed_set and m.startswith("image/"):
        return True
    if "audio" in allowed_set and (m.startswith("audio/") or ext in {"mp3", "wav", "m4a", "ogg", "aac", "flac", "opus", "amr", "3gp"}):
        return True
    if "video" in allowed_set and m.startswith("video/"):
        return True
    if "pdf" in allowed_set and (m == "application/pdf" or ext == "pdf"):
        return True
    if "txt" in allowed_set and ext == "txt":
        return True
    if "md" in allowed_set and ext in {"md", "markdown"}:
        return True
    # Permit direct extension allow matches, e.g., 'csv', 'json'
    if ext in allowed_set:
        return True
    return False


async def _stream_download(url: str, headers: dict, dest_path: Path, max_bytes: int) -> int:
    size = 0
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            # Pre-check content-length if present
            cl = resp.headers.get("Content-Length")
            if cl:
                try:
                    if int(cl) > max_bytes:
                        raise ValueError(f"Attachment too large (>{max_bytes} bytes)")
                except Exception:
                    pass
            async with aiofiles.open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(1024 * 64):
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        try:
                            await f.flush()
                        except Exception:
                            pass
                        try:
                            dest_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        raise ValueError(f"Attachment too large (>{max_bytes} bytes)")
                    await f.write(chunk)
    return size


async def download_slack_file(
    *,
    url_private_download: str,
    original_filename: Optional[str],
    mime: Optional[str],
    session_id: str,
    bot_token: str,
    max_bytes: int,
    allowed_types: Optional[Iterable[str]] = None,
) -> SavedAttachment:
    """Download a Slack file (via url_private_download) into the session attachments dir.

    Returns SavedAttachment with final path, or raises ValueError on validation issues.
    """
    filename = _sanitize_filename(original_filename or "attachment")
    if not _is_allowed_type(filename, mime, allowed_types):
        raise ValueError(f"Disallowed attachment type: {mime or filename}")

    dest_dir = get_session_attachments_dir(session_id)
    uid = uuid.uuid4().hex
    final_name = f"{uid}_{filename}"
    dest_path = dest_dir / final_name

    headers = {"Authorization": f"Bearer {bot_token}"}
    size = await _stream_download(url_private_download, headers, dest_path, max_bytes)

    return SavedAttachment(path=dest_path, filename=final_name, size=size, mime=mime)


async def download_all_from_event_files(
    files: Iterable[dict],
    session_id: str,
    bot_token: str,
    max_bytes: int,
    allowed_types: Optional[Iterable[str]] = None,
) -> Tuple[list[SavedAttachment], list[str]]:
    """Download all Slack file items from an event's files array.

    Returns (saved, errors) where saved is a list of SavedAttachment and errors are messages.
    """
    saved: list[SavedAttachment] = []
    errors: list[str] = []
    for f in files or []:
        try:
            url = f.get("url_private_download") or f.get("url_private")
            if not url:
                errors.append(f"No downloadable URL for file '{f.get('name')}'")
                continue
            name = f.get("name") or f.get("title") or "attachment"
            mime = f.get("mimetype")
            att = await download_slack_file(
                url_private_download=url,
                original_filename=name,
                mime=mime,
                session_id=session_id,
                bot_token=bot_token,
                max_bytes=max_bytes,
                allowed_types=allowed_types,
            )
            saved.append(att)
        except Exception as e:
            errors.append(f"{f.get('name') or 'attachment'}: {e}")
    return saved, errors

