from pathlib import Path
from datetime import datetime
import json
import uuid


def _load(path: Path):
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data["items"]
        if isinstance(data, list):
            return data
    except Exception:
        return []
    return []


def _save(path: Path, items):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schemaVersion": "JUNIPERUS_APPROVAL_QUEUE_v0_1_2",
        "updatedAt": datetime.utcnow().isoformat() + "Z",
        "items": items
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def create_approval(queue_path: str, kind: str, summary: str, risk: str, payload: dict):
    path = Path(queue_path)
    items = _load(path)
    item = {
        "id": "APR-" + uuid.uuid4().hex[:10].upper(),
        "kind": kind,
        "summary": summary,
        "risk": risk,
        "status": "PENDING",
        "createdAt": datetime.utcnow().isoformat() + "Z",
        "payload": payload or {}
    }
    items.append(item)
    _save(path, items)
    return item


def update_status(queue_path: str, approval_id: str, status: str):
    path = Path(queue_path)
    items = _load(path)
    found = None
    for item in items:
        if item.get("id") == approval_id:
            item["status"] = status
            item["updatedAt"] = datetime.utcnow().isoformat() + "Z"
            found = item
            break
    _save(path, items)
    return found
