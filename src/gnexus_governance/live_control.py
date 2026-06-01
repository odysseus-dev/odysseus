"""Gnexus live-control finalizer helpers for Juniperus."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


def repo_root_from_file(current_file: str | None = None) -> Path:
    """Resolve repository root from this module path."""
    path = Path(current_file or __file__).resolve()
    # src/gnexus_governance/live_control.py -> repo root
    return path.parents[2]


def data_root(repo_root: Path | None = None) -> Path:
    root = repo_root or repo_root_from_file()
    return root / "data" / "gnexus"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def load_live_control_state(repo_root: Path | None = None) -> Dict[str, Any]:
    root = repo_root or repo_root_from_file()
    gnx = data_root(root)
    state = _read_json(gnx / "mission-control" / "live-control-state.json", {})
    gates = _read_json(gnx / "live-control" / "activation-gates.json", {})
    matrix = _read_json(gnx / "live-control" / "authority-matrix.json", {})
    checklist = _read_json(gnx / "live-control" / "readiness-checklist.json", {})
    finalizer = _read_json(gnx / "live-control" / "finalizer-ledger.json", {})

    return {
        "status": finalizer.get("status") or state.get("status") or "unknown",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "route": "/gnexus/live-control",
        "workspaceRoot": state.get("workspaceRoot", "C:\\Users\\iamcy\\CymaticsDev"),
        "targetRepo": state.get("targetRepo", str(root)),
        "controlledWriteReady": bool(finalizer.get("controlledWriteReady", True)),
        "humanApprovalRequired": bool(finalizer.get("humanApprovalRequired", True)),
        "liveActivationEnabled": bool(finalizer.get("liveActivationEnabled", False)),
        "externalReadsEnabled": bool(finalizer.get("externalReadsEnabled", False)),
        "externalWritesEnabled": bool(finalizer.get("externalWritesEnabled", False)),
        "connectorCallsEnabled": bool(finalizer.get("connectorCallsEnabled", False)),
        "secretsStored": bool(finalizer.get("secretsStored", False)),
        "productionMutationLocked": bool(finalizer.get("productionMutationLocked", True)),
        "activationGates": gates.get("gates", []),
        "authorityMatrix": matrix.get("capabilities", {}),
        "readinessChecklist": checklist.get("items", []),
    }


def finalize_local_closeout(repo_root: Path | None = None) -> Dict[str, Any]:
    root = repo_root or repo_root_from_file()
    gnx = data_root(root)
    now = datetime.now(timezone.utc).isoformat()
    status = "JUNIPERUS_CONTROLLED_WRITE_LIVE_ACTIVATION_FINALIZER_READY_LOCAL_CLOSEOUT"

    finalizer_path = gnx / "live-control" / "finalizer-ledger.json"
    finalizer = _read_json(finalizer_path, {})
    finalizer.update({
        "status": status,
        "generatedAt": now,
        "controlledWriteReady": True,
        "humanApprovalRequired": True,
        "liveActivationEnabled": False,
        "externalReadsEnabled": False,
        "externalWritesEnabled": False,
        "connectorCallsEnabled": False,
        "secretsStored": False,
        "productionMutationLocked": True,
    })
    _write_json(finalizer_path, finalizer)

    state_path = gnx / "mission-control" / "live-control-state.json"
    state = _read_json(state_path, {})
    state.update({
        "status": status,
        "generatedAt": now,
        "route": "/gnexus/live-control",
        "targetRepo": str(root),
        "workspaceRoot": state.get("workspaceRoot", "C:\\Users\\iamcy\\CymaticsDev"),
        "controlledWriteReady": True,
        "humanApprovalRequired": True,
        "liveActivationEnabled": False,
        "restartRequired": True,
    })
    _write_json(state_path, state)

    receipt_path = gnx / "receipts" / "JUNIPERUS100-closeout.json"
    receipt = _read_json(receipt_path, {})
    receipt.update({
        "status": status,
        "generatedAt": now,
        "targetRepo": str(root),
        "route": "/gnexus/live-control",
        "boundary": {
            "humanApprovalRequired": True,
            "liveActivationEnabled": False,
            "externalReadsEnabled": False,
            "externalWritesEnabled": False,
            "connectorCallsEnabled": False,
            "secretsStored": False,
            "productionMutationLocked": True,
        },
    })
    _write_json(receipt_path, receipt)

    return load_live_control_state(root)
