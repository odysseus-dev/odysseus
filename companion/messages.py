"""Cross-platform message relay storage for the companion bridge.

Odysseus cannot send iMessage directly on Linux/Docker/Windows because Apple's
Messages app and AppleScript APIs only exist on macOS.  These helpers provide a
small owner-scoped outbox/inbox that a paired iPhone/iPad companion (or Shortcut)
can poll and fulfill, so the server itself does not need to be a Mac.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import RLock
import uuid

from src.constants import DATA_DIR

MESSAGES_FILE = os.path.join(DATA_DIR, "companion_messages.json")
_MAX_BODY_CHARS = 20_000
_LOCK = RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _blank_store() -> dict:
    return {"v": 1, "outbox": [], "inbox": []}


def _load_store() -> dict:
    path = Path(MESSAGES_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blank_store()
    if not isinstance(data, dict):
        return _blank_store()
    data.setdefault("v", 1)
    data.setdefault("outbox", [])
    data.setdefault("inbox", [])
    if not isinstance(data["outbox"], list):
        data["outbox"] = []
    if not isinstance(data["inbox"], list):
        data["inbox"] = []
    return data


def _save_store(data: dict) -> None:
    path = Path(MESSAGES_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _require_text(value, field: str, max_len: int = _MAX_BODY_CHARS) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    if len(text) > max_len:
        raise ValueError(f"{field} is too long")
    return text


def queue_outbound(owner: str, to: str, body: str, service: str = "imessage") -> dict:
    """Queue a message for a paired Apple device to send."""
    owner = _require_text(owner, "owner", 512)
    to = _require_text(to, "to", 512)
    body = _require_text(body, "body")
    service = (str(service or "imessage").strip().lower() or "imessage")
    if service not in {"imessage", "sms", "auto"}:
        raise ValueError("service must be imessage, sms, or auto")
    item = {
        "id": str(uuid.uuid4()),
        "owner": owner,
        "to": to,
        "body": body,
        "service": service,
        "status": "queued",
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
    }
    with _LOCK:
        data = _load_store()
        data["outbox"].append(item)
        _save_store(data)
    return dict(item)


def pending_outbound(owner: str, limit: int = 25) -> list[dict]:
    owner = _require_text(owner, "owner", 512)
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 25
    with _LOCK:
        data = _load_store()
        return [dict(m) for m in data["outbox"] if m.get("owner") == owner and m.get("status") == "queued"][:limit]


def mark_outbound(owner: str, message_id: str, status: str, error: str | None = None) -> dict:
    owner = _require_text(owner, "owner", 512)
    message_id = _require_text(message_id, "id", 128)
    status = str(status or "").strip().lower()
    if status not in {"sent", "failed"}:
        raise ValueError("status must be sent or failed")
    with _LOCK:
        data = _load_store()
        for msg in data["outbox"]:
            if msg.get("owner") == owner and msg.get("id") == message_id:
                msg["status"] = status
                msg["updated_at"] = _now_iso()
                if error:
                    msg["error"] = str(error)[:1000]
                _save_store(data)
                return dict(msg)
    raise KeyError(message_id)


def record_inbound(owner: str, sender: str, body: str, service: str = "imessage") -> dict:
    owner = _require_text(owner, "owner", 512)
    sender = _require_text(sender, "from", 512)
    body = _require_text(body, "body")
    item = {
        "id": str(uuid.uuid4()),
        "owner": owner,
        "from": sender,
        "body": body,
        "service": str(service or "imessage").strip().lower() or "imessage",
        "received_at": _now_iso(),
    }
    with _LOCK:
        data = _load_store()
        data["inbox"].append(item)
        _save_store(data)
    return dict(item)
