"""Gnexus Diff-First Code Editing Gate.

This module is intentionally conservative:
- it does not write target files;
- it creates patch proposals and queues them for human approval;
- patch application remains locked for a later package.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict


DEFAULT_WORKSPACE_ROOT = r"C:\Users\iamcy\CymaticsDev"
DATA_ROOT = Path("data") / "gnexus"
DIFF_ROOT = DATA_ROOT / "diff-gate"
PATCH_QUEUE = DIFF_ROOT / "patch-queue.json"
ROLLBACK_LEDGER = DIFF_ROOT / "rollback-ledger.json"
STATE_FILE = DATA_ROOT / "mission-control" / "diff-gate-state.json"

SENSITIVE_TERMS = [
    ".env", "auth.json", "app.db", "juniperus.db", "token", "secret",
    ".ssh", "appdata", "vault", "password", "private_key",
]


def _utc_now() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _ensure_files() -> None:
    DIFF_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not PATCH_QUEUE.exists():
        PATCH_QUEUE.write_text(json.dumps({"schema": "gnexus.patchQueue.v1", "items": []}, indent=2), encoding="utf-8")
    if not ROLLBACK_LEDGER.exists():
        ROLLBACK_LEDGER.write_text(json.dumps({"schema": "gnexus.rollbackLedger.v1", "items": []}, indent=2), encoding="utf-8")
    if not STATE_FILE.exists():
        STATE_FILE.write_text(json.dumps(get_state(), indent=2), encoding="utf-8")


def _read_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _workspace_root() -> Path:
    root = os.environ.get("GNEXUS_WORKSPACE_ROOT") or DEFAULT_WORKSPACE_ROOT
    return Path(root).resolve()


def _resolve_target(path: str) -> Path:
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except Exception:
        return False


def _is_sensitive(path: Path) -> bool:
    low = str(path).lower()
    return any(term.lower() in low for term in SENSITIVE_TERMS)


def _read_existing(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _make_diff(path: Path, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{path.name}",
        tofile=f"b/{path.name}",
        lineterm=""
    ))


def propose_write_file_diff(path: str, new_content: str, source: str = "write_file") -> Dict[str, Any]:
    """Queue a diff proposal instead of writing a file."""
    _ensure_files()
    target = _resolve_target(path)
    workspace = _workspace_root()

    if not _is_under(target, workspace):
        return {
            "ok": False,
            "blocked": True,
            "reason": "OUTSIDE_WORKSPACE",
            "error": f"Diff gate blocked write outside workspace: {target}",
            "workspaceRoot": str(workspace),
            "targetPath": str(target),
        }

    if _is_sensitive(target):
        return {
            "ok": False,
            "blocked": True,
            "reason": "SENSITIVE_PATH",
            "error": f"Diff gate blocked sensitive path write: {target}",
            "targetPath": str(target),
        }

    old = _read_existing(target)
    diff = _make_diff(target, old, new_content)
    content_hash = hashlib.sha256((str(target) + "\n" + new_content).encode("utf-8", errors="replace")).hexdigest()[:16]
    proposal_id = "diff-" + _dt.datetime.utcnow().strftime("%Y%m%d%H%M%S") + "-" + content_hash

    item = {
        "id": proposal_id,
        "type": "WRITE_FILE_DIFF_PROPOSAL",
        "status": "PENDING_HUMAN_APPROVAL",
        "createdAt": _utc_now(),
        "source": source,
        "targetPath": str(target),
        "workspaceRoot": str(workspace),
        "targetExists": target.exists(),
        "oldSize": len(old),
        "newSize": len(new_content),
        "sha256NewContent": hashlib.sha256(new_content.encode("utf-8", errors="replace")).hexdigest(),
        "diff": diff,
        "applyLocked": True,
        "humanApprovalRequired": True,
        "rollbackRequiredBeforeApply": True,
    }

    queue = _read_json(PATCH_QUEUE, {"schema": "gnexus.patchQueue.v1", "items": []})
    queue.setdefault("items", []).append(item)
    queue["updatedAt"] = _utc_now()
    _write_json(PATCH_QUEUE, queue)

    state = get_state()
    state["lastProposalId"] = proposal_id
    state["pendingCount"] = len([x for x in queue.get("items", []) if x.get("status") == "PENDING_HUMAN_APPROVAL"])
    state["updatedAt"] = _utc_now()
    _write_json(STATE_FILE, state)

    return {
        "ok": True,
        "queued": True,
        "proposalId": proposal_id,
        "status": "PENDING_HUMAN_APPROVAL",
        "targetPath": str(target),
        "message": "Diff proposal queued for human approval. Raw write_file did not execute.",
        "diffPreview": diff[:4000],
        "applyLocked": True,
    }


def list_patch_queue() -> Dict[str, Any]:
    _ensure_files()
    return _read_json(PATCH_QUEUE, {"schema": "gnexus.patchQueue.v1", "items": []})


def decide_patch(proposal_id: str, decision: str, actor: str = "operator", note: str = "") -> Dict[str, Any]:
    """Record approval/denial only. Does not apply patches."""
    _ensure_files()
    decision_u = (decision or "").strip().upper()
    if decision_u not in {"APPROVE", "DENY", "HOLD"}:
        return {"ok": False, "error": "decision must be APPROVE, DENY, or HOLD"}

    queue = list_patch_queue()
    found = None
    for item in queue.get("items", []):
        if item.get("id") == proposal_id:
            found = item
            break
    if not found:
        return {"ok": False, "error": f"proposal not found: {proposal_id}"}

    if decision_u == "APPROVE":
        found["status"] = "APPROVED_APPLY_LOCKED"
    elif decision_u == "DENY":
        found["status"] = "DENIED"
    else:
        found["status"] = "HOLD"

    found["decidedAt"] = _utc_now()
    found["decidedBy"] = actor
    found["decisionNote"] = note
    found["applyLocked"] = True
    queue["updatedAt"] = _utc_now()
    _write_json(PATCH_QUEUE, queue)

    return {
        "ok": True,
        "proposalId": proposal_id,
        "status": found["status"],
        "applyLocked": True,
        "message": "Decision recorded. Patch application remains locked until the approved apply stage.",
    }


def get_state() -> Dict[str, Any]:
    queue = _read_json(PATCH_QUEUE, {"schema": "gnexus.patchQueue.v1", "items": []})
    items = queue.get("items", [])
    return {
        "schema": "gnexus.diffGateState.v1",
        "status": "DIFF_FIRST_CODE_EDITING_GATE_ACTIVE_APPLY_LOCKED",
        "generatedAt": _utc_now(),
        "workspaceRoot": str(_workspace_root()),
        "patchQueue": str(PATCH_QUEUE),
        "proposalCount": len(items),
        "pendingCount": len([x for x in items if x.get("status") == "PENDING_HUMAN_APPROVAL"]),
        "approvedApplyLockedCount": len([x for x in items if x.get("status") == "APPROVED_APPLY_LOCKED"]),
        "applyLocked": True,
        "rawWriteFileDefault": "QUEUE_DIFF_FOR_HUMAN_APPROVAL",
        "governanceUrl": "http://127.0.0.1:7010/gnexus/diff-gate",
    }


_ensure_files()
