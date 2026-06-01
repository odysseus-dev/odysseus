"""Lightweight session persistence for the Odysseus CLI.

Conversations are saved as JSON under ~/.odysseus/sessions/ so a run can be
resumed later with full history. Sessions are keyed by a hash of the project
root, so `--resume` picks up the last conversation *for the current project*.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Dict, List, Optional

SESSIONS_DIR = Path.home() / ".odysseus" / "sessions"


def _root_key(root: Path) -> str:
    return hashlib.sha1(str(Path(root).resolve()).encode()).hexdigest()[:12]


def save(messages: List[Dict], model: str, root: Path,
         session_id: Optional[str] = None) -> Path:
    """Persist the conversation. Returns the file path written."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    key = _root_key(root)
    sid = session_id or f"{key}-{int(time.time())}"
    path = SESSIONS_DIR / f"{sid}.json"
    payload = {
        "id": sid,
        "root": str(Path(root).resolve()),
        "model": model,
        "updated": int(time.time()),
        "messages": messages,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def latest_for_root(root: Path) -> Optional[Path]:
    """Most recently updated session file for this project root, if any."""
    if not SESSIONS_DIR.is_dir():
        return None
    key = _root_key(root)
    candidates = sorted(
        SESSIONS_DIR.glob(f"{key}-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def load(path: Path) -> Dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def list_sessions(limit: int = 20) -> List[Dict]:
    """Recent sessions across all projects (newest first)."""
    if not SESSIONS_DIR.is_dir():
        return []
    files = sorted(SESSIONS_DIR.glob("*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    out = []
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({
                "id": d.get("id", f.stem),
                "root": d.get("root", "?"),
                "model": d.get("model", "?"),
                "messages": len(d.get("messages", [])),
                "updated": d.get("updated", 0),
            })
        except Exception:
            continue
    return out
