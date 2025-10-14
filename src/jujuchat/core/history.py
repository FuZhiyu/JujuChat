from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

_FILENAME_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _now_iso() -> str:
    return datetime.now().isoformat()


def _sanitize_filename(name: str) -> str:
    name = name.strip().replace(" ", "_")
    name = _FILENAME_SAFE_RE.sub("", name)
    return name or f"file_{int(time.time())}"


@dataclass
class AttachmentMeta:
    id: str
    filename: str
    path: str
    size: int
    mime: Optional[str]


class ChatHistoryManager:
    """Filesystem-backed chat history per session, with attachments subfolder.

    Layout:
      <history_dir>/<session_id>/
        messages.jsonl
        attachments/
        meta.json
    """

    def __init__(self, history_dir: Path):
        self.history_dir = Path(history_dir)
        self.history_dir.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    def session_dir(self, session_id: str) -> Path:
        d = self.history_dir / session_id
        (d / "attachments").mkdir(parents=True, exist_ok=True)
        return d

    def attachments_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "attachments"

    async def record_event(self, session_id: str, event: Dict[str, Any]) -> None:
        d = self.session_dir(session_id)
        line = json.dumps({
            "timestamp": _now_iso(),
            "session_id": session_id,
            **event,
        }, ensure_ascii=False)
        async with self._lock_for(session_id):
            import aiofiles
            async with aiofiles.open(d / "messages.jsonl", "a", encoding="utf-8") as f:
                await f.write(line + "\n")
            # update meta
            meta_path = d / "meta.json"
            meta = {
                "session_id": session_id,
                "last_activity_at": _now_iso(),
            }
            try:
                if meta_path.exists():
                    existing = json.loads(meta_path.read_text(encoding="utf-8"))
                    existing.update(meta)
                    meta = existing
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                # Non-fatal
                pass

    async def record_user(self, session_id: str, text: str, attachment_paths: Optional[List[str]] = None):
        await self.record_event(session_id, {
            "type": "user",
            "text": text,
            "attachment_paths": attachment_paths or [],
        })

    async def record_assistant(self, session_id: str, text: str):
        await self.record_event(session_id, {
            "type": "assistant",
            "text": text,
        })

    async def record_system(self, session_id: str, event_type: str, detail: Dict[str, Any] | None = None):
        await self.record_event(session_id, {
            "type": event_type,
            "detail": detail or {},
        })

    def _resolve_attachment_filename(self, filename: str, unique_id: str) -> str:
        safe = _sanitize_filename(filename)
        # Prefix with id to guarantee uniqueness
        if not safe:
            safe = unique_id
        return f"{unique_id}_{safe}"

    async def save_upload(self, session_id: str, filename: str, reader, max_size_bytes: int, mime: Optional[str] = None) -> AttachmentMeta:
        """Save an uploaded file from an async reader (implements .read).

        Returns AttachmentMeta with final path under session attachments dir.
        """
        import uuid
        dest_dir = self.attachments_dir(session_id)
        uid = uuid.uuid4().hex
        final_name = self._resolve_attachment_filename(filename or "attachment", uid)
        dest_path = dest_dir / final_name

        size = 0
        import aiofiles
        async with aiofiles.open(dest_path, "wb") as f:
            chunk_size = 1024 * 1024
            while True:
                chunk = await reader.read(chunk_size)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_size_bytes:
                    # Cleanup partial
                    try:
                        await f.flush()
                    except Exception:
                        pass
                    try:
                        dest_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    raise ValueError(f"Attachment too large (>{max_size_bytes} bytes)")
                await f.write(chunk)

        meta = AttachmentMeta(id=uid, filename=final_name, path=str(dest_path), size=size, mime=mime)
        await self.record_event(session_id, {
            "type": "attachment_saved",
            "filename": final_name,
            "path": str(dest_path),
            "size": size,
            "mime": mime,
        })
        return meta

    def list_sessions(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not self.history_dir.exists():
            return out
        for d in sorted(self.history_dir.iterdir()):
            if not d.is_dir():
                continue
            meta_path = d / "meta.json"
            meta = {"session_id": d.name}
            try:
                if meta_path.exists():
                    meta.update(json.loads(meta_path.read_text(encoding="utf-8")))
            except Exception:
                pass
            out.append(meta)
        out.sort(key=lambda m: m.get("last_activity_at", ""), reverse=True)
        return out

    def list_attachments(self, session_id: str) -> List[Dict[str, Any]]:
        dir_ = self.attachments_dir(session_id)
        items: List[Dict[str, Any]] = []
        if not dir_.exists():
            return items
        for p in sorted(dir_.iterdir()):
            if p.is_file():
                try:
                    st = p.stat()
                    items.append({
                        "filename": p.name,
                        "path": str(p),
                        "size": st.st_size,
                        "modified_at": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    })
                except Exception:
                    continue
        return items

    def validate_paths(self, session_id: str, paths: List[str]) -> List[str]:
        """Return absolute paths that are under the session history dir."""
        base = self.session_dir(session_id).resolve()
        out: List[str] = []
        for p in paths:
            try:
                abs_p = Path(p).expanduser().resolve()
                # Only allow reading under history_dir
                if str(abs_p).startswith(str(base)):
                    out.append(str(abs_p))
            except Exception:
                continue
        return out

    async def load_history(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        d = self.session_dir(session_id)
        path = d / "messages.jsonl"
        events: List[Dict[str, Any]] = []
        if not path.exists():
            return events
        try:
            # Read efficiently; for simplicity read all then slice tail
            lines = path.read_text(encoding="utf-8").splitlines()
            if limit is not None:
                lines = lines[-limit:]
            for line in lines:
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
        except Exception:
            pass
        return events

    async def reset_session(self, session_id: str) -> None:
        # Keep directory, remove messages file; attachments left intact unless policy says otherwise
        d = self.session_dir(session_id)
        try:
            (d / "messages.jsonl").unlink(missing_ok=True)
            await self.record_system(session_id, "session_reset", {})
        except Exception:
            pass

