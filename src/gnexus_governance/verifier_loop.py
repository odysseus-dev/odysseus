"""Gnexus verifier / repair / rollback loop for Juniperus.

Local-first queue/ledger layer only. This module records verification requests,
verification results, repair items, and rollback requests. It intentionally does
not execute shell commands or perform rollback mutation.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
import json
import uuid
DEFAULT_WORKSPACE_ROOT = Path(r"C:\Users\iamcy\CymaticsDev")
PACKAGE = "JUNIPERUS070"
STATUS = "JUNIPERUS_VERIFIER_REPAIR_ROLLBACK_LOOP_READY_LOCAL_CLOSEOUT"
def _repo_root() -> Path: return Path(__file__).resolve().parents[2]
def _data_root() -> Path: return _repo_root() / "data" / "gnexus" / "verifier-loop"
def _mission_state_path() -> Path: return _repo_root() / "data" / "gnexus" / "mission-control" / "verifier-loop-state.json"
def _utc() -> str: return datetime.now(timezone.utc).isoformat()
def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists(): return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception: return default
    return default
def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
def _append(path: Path, key: str, item: Dict[str, Any]) -> Dict[str, Any]:
    doc = _read_json(path, {key: []})
    if not isinstance(doc, dict): doc = {key: []}
    if key not in doc or not isinstance(doc[key], list): doc[key] = []
    doc[key].append(item)
    doc["updatedAt"] = _utc()
    _write_json(path, doc)
    return item
def _safe_id(prefix: str) -> str: return f"{prefix}-{uuid.uuid4().hex[:12]}"
def create_verification_request(change_id: str, target_path: str, verifier_hint: str = "", requested_by: str = "juniperus", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item = {"id": _safe_id("verify"), "type": "verification_request", "status": "PENDING_APPROVED_EXECUTION", "changeId": change_id, "targetPath": target_path, "verifierHint": verifier_hint, "requestedBy": requested_by, "metadata": metadata or {}, "createdAt": _utc(), "executionLocked": True, "requiresHumanApprovalForShell": True}
    return _append(_data_root() / "verification-queue.json", "requests", item)
def record_verification_result(request_id: str, passed: bool, summary: str, output_excerpt: str = "", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item = {"id": _safe_id("result"), "type": "verification_result", "requestId": request_id, "passed": bool(passed), "summary": summary, "outputExcerpt": output_excerpt[:4000], "metadata": metadata or {}, "createdAt": _utc()}
    _append(_data_root() / "verification-results.json", "results", item)
    if not passed:
        create_repair_item(source_id=item["id"], severity="repair_required", summary=f"Verification failed: {summary}", suggested_next_step="Inspect verifier output, create a patch proposal, and re-run verification after approval.", metadata={"requestId": request_id, **(metadata or {})})
    # Fire event for knowledge graph
    try:
        from src.event_bus import fire_event
        fire_event("verification_result", owner=metadata.get("owner") if metadata else None, **item)
    except Exception:
        logger.debug("verification_result event dispatch failed", exc_info=True)
    return item
def create_repair_item(source_id: str, severity: str, summary: str, suggested_next_step: str = "", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item = {"id": _safe_id("repair"), "type": "repair_item", "status": "OPEN", "sourceId": source_id, "severity": severity, "summary": summary, "suggestedNextStep": suggested_next_step, "metadata": metadata or {}, "createdAt": _utc(), "executionLocked": True}
    return _append(_data_root() / "repair-queue.json", "items", item)
def create_rollback_request(change_id: str, snapshot_id: str, reason: str, requested_by: str = "juniperus", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    item = {"id": _safe_id("rollback"), "type": "rollback_request", "status": "PENDING_HUMAN_APPROVAL", "changeId": change_id, "snapshotId": snapshot_id, "reason": reason, "requestedBy": requested_by, "metadata": metadata or {}, "createdAt": _utc(), "autoRollback": False, "executionLocked": True}
    return _append(_data_root() / "rollback-requests.json", "requests", item)
def state() -> Dict[str, Any]:
    queue = _read_json(_data_root() / "verification-queue.json", {"requests": []})
    results = _read_json(_data_root() / "verification-results.json", {"results": []})
    repairs = _read_json(_data_root() / "repair-queue.json", {"items": []})
    rollbacks = _read_json(_data_root() / "rollback-requests.json", {"requests": []})
    mission = _read_json(_mission_state_path(), {})
    return {"status": STATUS, "package": PACKAGE, "generatedAt": _utc(), "workspaceRoot": str(DEFAULT_WORKSPACE_ROOT), "counts": {"verificationRequests": len(queue.get("requests", [])) if isinstance(queue, dict) else 0, "verificationResults": len(results.get("results", [])) if isinstance(results, dict) else 0, "repairItems": len(repairs.get("items", [])) if isinstance(repairs, dict) else 0, "rollbackRequests": len(rollbacks.get("requests", [])) if isinstance(rollbacks, dict) else 0}, "boundary": {"autoExecute": False, "autoRollback": False, "externalReads": False, "externalWrites": False, "connectorCalls": False, "secretsStored": False, "humanApprovalRequiredForShell": True}, "queue": queue, "results": results, "repairs": repairs, "rollbacks": rollbacks, "missionControl": mission}
