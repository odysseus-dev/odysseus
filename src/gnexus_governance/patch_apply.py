"""Gnexus approved patch apply + rollback executor.

JUNIPERUS060 installs a controlled apply layer for patches that were already
proposed by the diff gate and approved by the human decision layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKSPACE_ROOT = Path(os.getenv("GNEXUS_WORKSPACE_ROOT", r"C:\Users\iamcy\CymaticsDev")).resolve()

BLOCKED_FRAGMENTS = (
    ".env",
    "auth.json",
    "app.db",
    "juniperus.db",
    "tokens",
    "secrets",
    ".ssh",
    "appdata",
    "vault",
    "private_key",
    "id_rsa",
    "id_ed25519",
)

APPROVED_STATUSES = {"approved", "human_approved", "ready_to_apply"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _data_root() -> Path:
    root = _repo_root() / "data" / "gnexus"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _json_read(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return fallback


def _json_write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _normalize_target_path(path_value: str) -> Path:
    raw = (path_value or "").strip().strip('"')
    if not raw:
        raise ValueError("target path is required")
    p = Path(raw)
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p
    return p.resolve()


def _assert_path_allowed(path: Path) -> None:
    try:
        path.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise PermissionError("target path is outside the configured workspace root")
    lower = str(path).lower()
    for frag in BLOCKED_FRAGMENTS:
        if frag.lower() in lower:
            raise PermissionError("target path is sensitive and blocked by policy")


def _patch_queue_path() -> Path:
    return _data_root() / "diff-gate" / "patch-queue.json"


def _apply_ledger_path() -> Path:
    return _data_root() / "patch-apply" / "apply-ledger.json"


def _rollback_index_path() -> Path:
    return _data_root() / "patch-apply" / "rollback-snapshots.json"


def _state_path() -> Path:
    return _data_root() / "mission-control" / "patch-apply-state.json"


def _snapshot_dir() -> Path:
    root = _data_root() / "patch-apply" / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _extract_entries(queue: Any) -> List[Dict[str, Any]]:
    if isinstance(queue, list):
        return [x for x in queue if isinstance(x, dict)]
    if isinstance(queue, dict):
        for key in ("patches", "items", "entries", "queue"):
            value = queue.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def list_patch_queue() -> Dict[str, Any]:
    queue = _json_read(_patch_queue_path(), {"patches": []})
    entries = _extract_entries(queue)
    return {
        "workspaceRoot": str(WORKSPACE_ROOT),
        "patchQueuePath": str(_patch_queue_path()),
        "count": len(entries),
        "approvedCount": len([x for x in entries if str(x.get("status") or x.get("decision") or "").lower() in APPROVED_STATUSES]),
        "entries": entries,
    }


def get_state() -> Dict[str, Any]:
    state = _json_read(_state_path(), {})
    queue = list_patch_queue()
    ledger = _json_read(_apply_ledger_path(), {"entries": []})
    snapshots = _json_read(_rollback_index_path(), {"snapshots": []})
    return {
        "status": state.get("status", "JUNIPERUS_PATCH_APPLY_STATE_AVAILABLE"),
        "stage": "JUNIPERUS060",
        "workspaceRoot": str(WORKSPACE_ROOT),
        "autoApply": False,
        "applyRequiresApproval": True,
        "rollbackRequired": True,
        "patchQueue": queue,
        "applyLedgerCount": len(ledger.get("entries", [])) if isinstance(ledger, dict) else 0,
        "rollbackSnapshotCount": len(snapshots.get("snapshots", [])) if isinstance(snapshots, dict) else 0,
        "routes": {
            "ui": "/gnexus/patch-apply",
            "state": "/api/gnexus/patch-apply/state",
            "apply": "/api/gnexus/patch-apply/apply",
        },
    }


def _find_patch(patch_id: str) -> Dict[str, Any]:
    if not patch_id:
        raise ValueError("patch_id is required")
    entries = list_patch_queue()["entries"]
    for item in entries:
        candidates = [item.get("id"), item.get("patchId"), item.get("patch_id"), item.get("operationId")]
        if patch_id in [str(x) for x in candidates if x is not None]:
            return item
    raise KeyError("approved patch not found in patch queue")


def _extract_new_content(item: Dict[str, Any]) -> str:
    for key in ("proposedContent", "newContent", "content", "body", "after"):
        if key in item and item[key] is not None:
            return str(item[key])
    raise ValueError("patch item does not contain proposed file content; unified-diff-only application is reserved for a later executor")


def _extract_path(item: Dict[str, Any]) -> str:
    for key in ("path", "targetPath", "filePath", "target", "file"):
        if key in item and item[key]:
            return str(item[key])
    raise ValueError("patch item does not contain a target path")


def _is_approved(item: Dict[str, Any]) -> bool:
    status = str(item.get("status") or item.get("decision") or item.get("approvalStatus") or "").lower()
    return status in APPROVED_STATUSES or bool(item.get("approved") is True or item.get("humanApproved") is True)


def apply_approved_patch(patch_id: str, *, confirm: bool = False, actor: str = "human") -> Dict[str, Any]:
    """Apply an approved patch by writing proposed content to the target path.

    This function intentionally does not apply arbitrary unified diffs yet. It
    requires a queue item with a full proposed/new content body, which allows a
    deterministic rollback snapshot and hash record.
    """
    if not confirm:
        return {"ok": False, "requiresConfirmation": True, "error": "confirm=true is required before applying an approved patch"}

    item = _find_patch(patch_id)
    if not _is_approved(item):
        return {"ok": False, "error": "patch is not approved for application", "patch": item}

    target = _normalize_target_path(_extract_path(item))
    _assert_path_allowed(target)
    new_content = _extract_new_content(item)

    old_content = ""
    existed = target.exists()
    if existed:
        old_content = target.read_text(encoding="utf-8", errors="replace")

    snapshot_id = "rollback-" + uuid.uuid4().hex[:12]
    snapshot_path = _snapshot_dir() / (snapshot_id + ".json")
    snapshot = {
        "snapshotId": snapshot_id,
        "patchId": patch_id,
        "targetPath": str(target),
        "existedBefore": existed,
        "oldSha256": _sha256_text(old_content),
        "oldContent": old_content,
        "createdAt": _utc_now(),
        "actor": actor,
    }
    _json_write(snapshot_path, snapshot)

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding="utf-8")

    entry = {
        "applyId": "apply-" + uuid.uuid4().hex[:12],
        "patchId": patch_id,
        "targetPath": str(target),
        "actor": actor,
        "appliedAt": _utc_now(),
        "oldSha256": snapshot["oldSha256"],
        "newSha256": _sha256_text(new_content),
        "rollbackSnapshot": str(snapshot_path),
        "status": "applied_with_rollback_snapshot",
    }

    ledger = _json_read(_apply_ledger_path(), {"version": "0.1.0", "stage": "JUNIPERUS060", "entries": []})
    if not isinstance(ledger, dict):
        ledger = {"version": "0.1.0", "stage": "JUNIPERUS060", "entries": []}
    ledger.setdefault("entries", []).append(entry)
    _json_write(_apply_ledger_path(), ledger)

    index = _json_read(_rollback_index_path(), {"version": "0.1.0", "stage": "JUNIPERUS060", "snapshots": []})
    if not isinstance(index, dict):
        index = {"version": "0.1.0", "stage": "JUNIPERUS060", "snapshots": []}
    index.setdefault("snapshots", []).append({
        "snapshotId": snapshot_id,
        "patchId": patch_id,
        "targetPath": str(target),
        "snapshotPath": str(snapshot_path),
        "createdAt": snapshot["createdAt"],
    })
    _json_write(_rollback_index_path(), index)

    return {"ok": True, "entry": entry}


def restore_snapshot(snapshot_id: str, *, confirm: bool = False, actor: str = "human") -> Dict[str, Any]:
    if not confirm:
        return {"ok": False, "requiresConfirmation": True, "error": "confirm=true is required before rollback"}
    if not snapshot_id:
        raise ValueError("snapshot_id is required")
    snapshot_path = _snapshot_dir() / (snapshot_id + ".json")
    snapshot = _json_read(snapshot_path, None)
    if not isinstance(snapshot, dict):
        raise FileNotFoundError("rollback snapshot not found")
    target = _normalize_target_path(snapshot.get("targetPath", ""))
    _assert_path_allowed(target)
    if snapshot.get("existedBefore"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(snapshot.get("oldContent", "")), encoding="utf-8")
    elif target.exists():
        target.unlink()
    return {
        "ok": True,
        "snapshotId": snapshot_id,
        "targetPath": str(target),
        "restoredAt": _utc_now(),
        "actor": actor,
    }
