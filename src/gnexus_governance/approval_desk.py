from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data" / "gnexus"
DESK_DIR = DATA_DIR / "approval-desk"
DECISION_LEDGER = DESK_DIR / "decision-ledger.json"
APPROVAL_QUEUE = DATA_DIR / "approval-queue.json"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def ensure_ledgers() -> Dict[str, Any]:
    DESK_DIR.mkdir(parents=True, exist_ok=True)
    ledger = read_json(DECISION_LEDGER, None)
    if not isinstance(ledger, dict):
        ledger = {
            "schemaVersion": "0.1.1",
            "status": "ready",
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "decisions": []
        }
        write_json(DECISION_LEDGER, ledger)
    queue = read_json(APPROVAL_QUEUE, None)
    if not isinstance(queue, dict):
        queue = {
            "schemaVersion": "0.1.1",
            "status": "ready",
            "createdAt": now_iso(),
            "updatedAt": now_iso(),
            "items": []
        }
        write_json(APPROVAL_QUEUE, queue)
    return {"decisionLedger": ledger, "approvalQueue": queue}


def list_state() -> Dict[str, Any]:
    ledgers = ensure_ledgers()
    decisions: List[Dict[str, Any]] = ledgers["decisionLedger"].get("decisions", [])
    items: List[Dict[str, Any]] = ledgers["approvalQueue"].get("items", [])
    pending = [item for item in items if item.get("state", "pending") == "pending"]
    return {
        "schemaVersion": "0.1.1",
        "status": "JUNIPERUS_APPROVAL_DESK_READY",
        "generatedAt": now_iso(),
        "executionUnlocked": False,
        "shellInterceptionActive": False,
        "fileInterceptionActive": False,
        "pendingCount": len(pending),
        "decisionCount": len(decisions),
        "pending": pending,
        "decisions": decisions[-50:],
    }


def propose(payload: Dict[str, Any]) -> Dict[str, Any]:
    ledgers = ensure_ledgers()
    item = {
        "id": "approval-" + uuid.uuid4().hex[:12],
        "state": "pending",
        "createdAt": now_iso(),
        "riskBand": payload.get("riskBand", "medium"),
        "operationType": payload.get("operationType", "unspecified"),
        "title": payload.get("title", "Untitled proposed operation"),
        "summary": payload.get("summary", ""),
        "target": payload.get("target", ""),
        "requiresVerifier": bool(payload.get("requiresVerifier", True)),
        "raw": payload,
    }
    queue = ledgers["approvalQueue"]
    queue.setdefault("items", []).append(item)
    queue["updatedAt"] = now_iso()
    write_json(APPROVAL_QUEUE, queue)
    return item


def decide(payload: Dict[str, Any]) -> Dict[str, Any]:
    ledgers = ensure_ledgers()
    approval_id = str(payload.get("id", "")).strip()
    decision = str(payload.get("decision", "")).strip().lower()
    if decision not in {"approved", "denied"}:
        raise ValueError("decision must be approved or denied")
    queue = ledgers["approvalQueue"]
    matched = None
    for item in queue.get("items", []):
        if item.get("id") == approval_id:
            item["state"] = decision
            item["decidedAt"] = now_iso()
            item["decisionReason"] = payload.get("reason", "")
            matched = item
            break
    if matched is None:
        raise ValueError("approval item not found")
    queue["updatedAt"] = now_iso()
    write_json(APPROVAL_QUEUE, queue)

    ledger = ledgers["decisionLedger"]
    record = {
        "id": "decision-" + uuid.uuid4().hex[:12],
        "approvalId": approval_id,
        "decision": decision,
        "reason": payload.get("reason", ""),
        "decidedAt": now_iso(),
        "item": matched,
    }
    ledger.setdefault("decisions", []).append(record)
    ledger["updatedAt"] = now_iso()
    write_json(DECISION_LEDGER, ledger)
    return record
