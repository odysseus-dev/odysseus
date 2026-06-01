"""Gnexus / Juniperus operator loop.

Local-first orchestration layer that links App Dock, Approval Desk,
Interceptor, Diff Gate, Patch Apply, and Verifier Loop ledgers.

This module intentionally does not execute shell commands or apply patches.
It creates operation plans, ledgers, and Mission Control state objects.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


STATUS = "JUNIPERUS_FULL_OPERATOR_LOOP_READY_LOCAL_CLOSEOUT"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    return repo_root() / "data" / "gnexus"


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def operator_paths() -> Dict[str, str]:
    root = data_root()
    return {
        "operatorQueue": str(root / "operator-loop" / "operator-queue.json"),
        "operationLedger": str(root / "operator-loop" / "operation-ledger.json"),
        "operatorRunbook": str(root / "operator-loop" / "operator-runbook.json"),
        "approvalQueue": str(root / "approval-queue.json"),
        "appRegistry": str(root / "app-registry.json"),
        "patchQueue": str(root / "diff-gate" / "patch-queue.json"),
        "applyLedger": str(root / "patch-apply" / "apply-ledger.json"),
        "verificationQueue": str(root / "verifier-loop" / "verification-queue.json"),
        "repairQueue": str(root / "verifier-loop" / "repair-queue.json"),
        "rollbackRequests": str(root / "verifier-loop" / "rollback-requests.json"),
        "missionControl": str(root / "mission-control" / "operator-loop-state.json"),
    }


def load_state() -> Dict[str, Any]:
    paths = operator_paths()
    queue = _read_json(Path(paths["operatorQueue"]), {"items": []})
    ledger = _read_json(Path(paths["operationLedger"]), {"items": []})
    runbook = _read_json(Path(paths["operatorRunbook"]), {"steps": []})
    apps = _read_json(Path(paths["appRegistry"]), {"apps": [], "items": []})
    approvals = _read_json(Path(paths["approvalQueue"]), {"items": []})
    patches = _read_json(Path(paths["patchQueue"]), {"items": []})
    verifications = _read_json(Path(paths["verificationQueue"]), {"items": []})
    repairs = _read_json(Path(paths["repairQueue"]), {"items": []})
    rollbacks = _read_json(Path(paths["rollbackRequests"]), {"items": []})

    app_items = apps.get("apps") if isinstance(apps, dict) else []
    if not app_items and isinstance(apps, dict):
        app_items = apps.get("items", [])

    state = {
        "status": STATUS,
        "generatedAt": _utc_now(),
        "package": "JUNIPERUS080",
        "workspaceRoot": "C:\\Users\\iamcy\\CymaticsDev",
        "targetRepo": "C:\\Users\\iamcy\\CymaticsDev\\00_SYSTEMS\\Juniperus",
        "routes": {
            "operatorLoop": "/gnexus/operator-loop",
            "apiState": "/api/gnexus/operator-loop/state",
            "apiCreatePlan": "/api/gnexus/operator-loop/plan",
        },
        "counts": {
            "operatorQueue": len(queue.get("items", [])) if isinstance(queue, dict) else 0,
            "operationLedger": len(ledger.get("items", [])) if isinstance(ledger, dict) else 0,
            "apps": len(app_items or []),
            "approvals": len(approvals.get("items", [])) if isinstance(approvals, dict) else 0,
            "patches": len(patches.get("items", [])) if isinstance(patches, dict) else 0,
            "verificationQueue": len(verifications.get("items", [])) if isinstance(verifications, dict) else 0,
            "repairQueue": len(repairs.get("items", [])) if isinstance(repairs, dict) else 0,
            "rollbackRequests": len(rollbacks.get("items", [])) if isinstance(rollbacks, dict) else 0,
        },
        "boundary": {
            "autoExecute": False,
            "autoApplyPatch": False,
            "autoRollback": False,
            "humanApprovalRequired": True,
            "externalReads": False,
            "externalWrites": False,
            "connectorCalls": False,
            "secretsStored": False,
        },
        "runbook": runbook,
        "paths": paths,
    }
    _write_json(Path(paths["missionControl"]), state)
    return state


def create_operation_plan(
    intent: str,
    app_id: Optional[str] = None,
    operation_type: str = "inspect_plan",
    requested_by: str = "local-user",
) -> Dict[str, Any]:
    """Create a proposal-only operation plan.

    The plan deliberately stops at approval/diff/verifier queue boundaries.
    No runtime command is executed here.
    """
    if not intent or not intent.strip():
        raise ValueError("intent is required")

    op_id = "op-" + uuid.uuid4().hex[:12]
    now = _utc_now()
    plan = {
        "id": op_id,
        "createdAt": now,
        "updatedAt": now,
        "requestedBy": requested_by,
        "intent": intent.strip(),
        "appId": app_id,
        "operationType": operation_type,
        "status": "approval_required",
        "mode": "proposal_only",
        "stages": [
            {"name": "intent_received", "status": "complete", "at": now},
            {"name": "operation_planned", "status": "complete", "at": now},
            {"name": "approval_requested", "status": "pending", "at": now},
            {"name": "diff_proposed", "status": "waiting_if_needed"},
            {"name": "patch_apply", "status": "locked_until_approved"},
            {"name": "verification_requested", "status": "waiting_until_apply"},
            {"name": "repair_or_closeout", "status": "waiting_until_verification"},
        ],
        "requires": {
            "humanApproval": True,
            "diffFirstForWrites": True,
            "rollbackSnapshotBeforeMutation": True,
            "verificationAfterMutation": True,
        },
        "blocked": {
            "autoExecute": True,
            "autoApplyPatch": True,
            "autoRollback": True,
            "externalReads": True,
            "externalWrites": True,
            "connectorCalls": True,
            "secretStorage": True,
        },
        "links": {
            "approvalDesk": "/gnexus/approval-desk",
            "appDock": "/gnexus/app-dock",
            "interceptor": "/gnexus/interceptor",
            "diffGate": "/gnexus/diff-gate",
            "patchApply": "/gnexus/patch-apply",
            "verifierLoop": "/gnexus/verifier-loop",
        },
    }

    paths = operator_paths()
    queue_path = Path(paths["operatorQueue"])
    ledger_path = Path(paths["operationLedger"])

    queue = _read_json(queue_path, {"items": []})
    ledger = _read_json(ledger_path, {"items": []})
    if not isinstance(queue, dict):
        queue = {"items": []}
    if not isinstance(ledger, dict):
        ledger = {"items": []}

    queue.setdefault("items", []).append(plan)
    ledger.setdefault("items", []).append({
        "id": "ledger-" + uuid.uuid4().hex[:12],
        "operationId": op_id,
        "event": "operation_plan_created",
        "at": now,
        "status": "approval_required",
        "intent": intent.strip(),
        "appId": app_id,
    })

    _write_json(queue_path, queue)
    _write_json(ledger_path, ledger)
    load_state()
    return plan


def initialize_operator_loop_files() -> Dict[str, Any]:
    paths = operator_paths()
    defaults = {
        "operatorQueue": {
            "schema": "gnexus.operator_loop.queue.v1",
            "package": "JUNIPERUS080",
            "items": [],
        },
        "operationLedger": {
            "schema": "gnexus.operator_loop.ledger.v1",
            "package": "JUNIPERUS080",
            "items": [],
        },
        "operatorRunbook": {
            "schema": "gnexus.operator_loop.runbook.v1",
            "package": "JUNIPERUS080",
            "steps": [
                "Select workspace/app.",
                "Create operation plan.",
                "Classify risk.",
                "Request human approval.",
                "Use diff gate for code changes.",
                "Use patch apply only after approval and rollback snapshot.",
                "Queue verifier run.",
                "Route failures to repair queue.",
                "Close with receipt only after verifier pass."
            ],
        },
    }
    for key, payload in defaults.items():
        p = Path(paths[key])
        if not p.exists():
            _write_json(p, payload)
    return load_state()
