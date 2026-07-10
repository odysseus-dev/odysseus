"""Structured, local-only Misumi event logging."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from src.constants import DATA_DIR


EVENT_FIELDS = (
    "request_id", "persona", "selected_skill_ids", "selected_tools",
    "blocked_tools", "task_id", "files_read", "files_changed", "model",
    "backend", "latency_ms", "outcome", "error", "blocker", "approval_mode",
)


class MisumiEventLog:
    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path or os.getenv("MISUMI_EVENT_LOG") or Path(DATA_DIR) / "misumi" / "events.jsonl")
        self._lock = threading.Lock()

    @staticmethod
    def request_id() -> str:
        return uuid.uuid4().hex

    def emit(self, event: Dict[str, object]) -> Dict[str, object]:
        record = {field: event.get(field) for field in EVENT_FIELDS}
        record.update({"timestamp": time.time(), "event": event.get("event") or "request"})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return record

    def recent(self, limit: int = 50) -> List[Dict[str, object]]:
        if not self.path.is_file():
            return []
        lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        result = []
        for line in lines[-max(1, min(int(limit), 500)):]:
            try:
                value = json.loads(line)
                if isinstance(value, dict):
                    result.append(value)
            except json.JSONDecodeError:
                continue
        return result
