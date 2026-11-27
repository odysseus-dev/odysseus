"""Copy a file before the agent rewrites it, and restore the latest copy.

One request id shares a directory; rewind restores that directory's files.
Files that did not exist before the turn are left in place.

Restoring is an explicit write, so it is checked like any other: a destination
that the state gate protects is skipped instead of copied. Entries in the
manifest are only trusted for blob names inside the bucket, so a crafted
manifest cannot copy arbitrary files.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.constants import AGENT_WORKSPACE_DIR


def _slug(value: str) -> str:
    raw = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (value or "anon"))
    return raw[:80] or "anon"


def _root(owner: str, session_id: str) -> Path:
    return Path(AGENT_WORKSPACE_DIR) / "checkpoints" / _slug(owner) / _slug(session_id)


def _dest_blocked(dest: str) -> Optional[str]:
    """The same guard the write tools use; a rewind must not bypass it."""
    try:
        from src.state_gate import check_path
    except Exception:  # pragma: no cover - the guard is optional at import time
        return None
    return check_path(dest, action="rewind")


def _read_manifest(path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


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
        manifest = _read_manifest(manifest_path)
        abs_path = str(target.resolve())
        if any(item.get("path") == abs_path for item in manifest):
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
        return {"ok": False, "error": "no checkpoint for this session", "restored": [], "skipped": []}
    buckets = [p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()]
    if not buckets:
        return {"ok": False, "error": "no checkpoint for this session", "restored": [], "skipped": []}
    latest = max(buckets, key=lambda p: p.stat().st_mtime)
    manifest = _read_manifest(latest / "manifest.json")
    if not manifest:
        return {"ok": False, "error": "checkpoint is unreadable", "restored": [], "skipped": []}

    restored: List[str] = []
    skipped: List[Dict[str, str]] = []
    bucket_root = latest.resolve()
    for item in manifest:
        dest = item.get("path")
        blob_name = item.get("blob")
        if not isinstance(dest, str) or not isinstance(blob_name, str):
            continue
        if not Path(dest).is_absolute() or Path(blob_name).name != blob_name:
            skipped.append({"path": dest, "reason": "invalid manifest entry"})
            continue
        blob = latest / blob_name
        try:
            if not blob.is_file() or bucket_root not in blob.resolve().parents:
                skipped.append({"path": dest, "reason": "blob is outside the checkpoint"})
                continue
        except OSError:
            skipped.append({"path": dest, "reason": "blob is unreadable"})
            continue
        blocked = _dest_blocked(dest)
        if blocked:
            skipped.append({"path": dest, "reason": blocked})
            continue
        try:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(blob, dest)
        except OSError as exc:
            skipped.append({"path": dest, "reason": str(exc)})
            continue
        restored.append(dest)
    return {"ok": True, "restored": restored, "skipped": skipped, "bucket": latest.name}
