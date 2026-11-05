"""Copy a file before the agent rewrites it, and restore the latest copy.

One request id shares a directory. Rewind restores that directory's files.
Files that did not exist before the turn are left in place.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from src.constants import AGENT_WORKSPACE_DIR


def _slug(value: str) -> str:
    raw = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (value or "anon"))
    return raw[:80] or "anon"


def _root(owner: str, session_id: str) -> Path:
    return Path(AGENT_WORKSPACE_DIR) / "checkpoints" / _slug(owner) / _slug(session_id)


def snapshot_existing(path: str, ctx: Optional[Dict[str, Any]] = None) -> Optional[str]:
    ctx = ctx or {}
    target = Path(path)
    try:
        if not target.is_file():
            return None
    except OSError:
        return None
    session_id = str(ctx.get("session_id") or "nosess")
    owner = str(ctx.get("owner") or "")
    turn = str(ctx.get("request_id") or ctx.get("turn_id") or "turn")
    bucket = _root(owner, session_id) / _slug(turn)
    try:
        bucket.mkdir(parents=True, exist_ok=True)
        manifest_path = bucket / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = []
        if not isinstance(manifest, list):
            manifest = []
        abs_path = str(target.resolve())
        if any(isinstance(item, dict) and item.get("path") == abs_path for item in manifest):
            return abs_path
        blob = bucket / f"{len(manifest):04d}.bin"
        shutil.copy2(target, blob)
        manifest.append({"path": abs_path, "blob": blob.name})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return abs_path
    except OSError:
        return None


def rewind_latest(owner: str, session_id: str) -> Dict[str, Any]:
    root = _root(owner, session_id)
    if not root.is_dir():
        return {"ok": False, "error": "no checkpoint for this session", "restored": []}
    buckets = [p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    if not buckets:
        return {"ok": False, "error": "no checkpoint for this session", "restored": []}
    latest = max(buckets, key=lambda p: p.stat().st_mtime)
    try:
        manifest = json.loads((latest / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": False, "error": "checkpoint is unreadable", "restored": []}
    restored = []
    for item in manifest:
        if not isinstance(item, dict):
            continue
        dest = item.get("path")
        blob_name = item.get("blob")
        if not isinstance(dest, str) or not isinstance(blob_name, str):
            continue
        blob = latest / blob_name
        if not blob.is_file():
            continue
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(blob, dest)
        restored.append(dest)
    return {"ok": True, "restored": restored, "bucket": latest.name}
