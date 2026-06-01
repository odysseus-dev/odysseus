from pathlib import Path
from datetime import datetime
import json
import uuid


def write_receipt(receipts_path: str, operation: str, status: str, payload: dict):
    path = Path(receipts_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {"items": []}
    else:
        data = {"schemaVersion": "JUNIPERUS_OPERATION_RECEIPTS_v0_1_2", "items": []}
    item = {
        "id": "RCT-" + uuid.uuid4().hex[:10].upper(),
        "operation": operation,
        "status": status,
        "createdAt": datetime.utcnow().isoformat() + "Z",
        "payload": payload or {}
    }
    data.setdefault("items", []).append(item)
    data["updatedAt"] = datetime.utcnow().isoformat() + "Z"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return item
