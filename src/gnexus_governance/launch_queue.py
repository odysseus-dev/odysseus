"""Governed launch proposal queue for Juniperus App Dock."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")

def propose_launch(repo_root: str, app_id: str, command_key: str = "start", requested_by: str = "user") -> dict[str, Any]:
    repo = Path(repo_root)
    registry_path = repo / "data" / "gnexus" / "app-registry.json"
    queue_path = repo / "data" / "gnexus" / "app-dock" / "launch-queue.json"
    approval_path = repo / "data" / "gnexus" / "approval-queue.json"

    registry = _read_json(registry_path, {"apps": []})
    apps = registry.get("apps") if isinstance(registry.get("apps"), list) else []
    app = next((a for a in apps if a.get("id") == app_id), None)
    if not app:
        return {"ok": False, "error": "APP_NOT_FOUND", "appId": app_id}

    command = (app.get("commands") or {}).get(command_key)
    if not command:
        return {"ok": False, "error": "COMMAND_NOT_FOUND", "appId": app_id, "commandKey": command_key}

    item = {
        "id": "launch-" + uuid.uuid4().hex[:12],
        "type": "RUNTIME_LAUNCH_PROPOSAL",
        "status": "PENDING_HUMAN_APPROVAL",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "requestedBy": requested_by,
        "appId": app_id,
        "appName": app.get("name"),
        "root": app.get("root"),
        "commandKey": command_key,
        "command": command,
        "risk": "MEDIUM",
        "executionEnabled": False,
        "approvalRequired": True,
        "note": "JUNIPERUS020 records launch proposals only. Execution is reserved for a later approved runtime package."
    }

    launch_queue = _read_json(queue_path, {"schema": "gnexus.launch-queue.v1", "items": []})
    if not isinstance(launch_queue.get("items"), list):
        launch_queue["items"] = []
    launch_queue["items"].append(item)
    _write_json(queue_path, launch_queue)

    approval_queue = _read_json(approval_path, {"schema": "gnexus.approval-queue.v1", "items": []})
    if not isinstance(approval_queue.get("items"), list):
        approval_queue["items"] = []
    approval_queue["items"].append(item)
    _write_json(approval_path, approval_queue)

    return {"ok": True, "proposal": item}
