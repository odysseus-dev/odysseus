from __future__ import annotations

import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def receipt(contract, agent: str, action: str, source: Path | None, destination: Path | None, digest: str | None, exit_code: int, status: str) -> dict:
    from datetime import datetime, timezone
    return {"task_id": contract.task_id, "agent": agent, "action": action, "source": str(source) if source else None, "destination": str(destination) if destination else None, "sha256": digest, "exit_code": exit_code, "utc": datetime.now(timezone.utc).isoformat(), "status": status}
