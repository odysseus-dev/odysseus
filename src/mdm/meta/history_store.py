import json
import time
from typing import Dict, List, Optional


class HistoryStore:
    def __init__(self, path: str = None):
        self._events: List[dict] = []
        self._path = path

    def log(self, event_type: str, data: dict):
        entry = {
            "type": event_type,
            "timestamp": time.time(),
            "data": data,
        }
        self._events.append(entry)

    def get_events(self, event_type: Optional[str] = None, limit: int = 100) -> list:
        if event_type:
            filtered = [e for e in self._events if e["type"] == event_type]
        else:
            filtered = list(self._events)
        return filtered[-limit:]

    def get_latest(self, event_type: str, n: int = 5) -> list:
        return self.get_events(event_type, limit=n)

    def persist(self, path: str = None):
        p = path or self._path
        if p:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self._events[-500:], f, indent=2, default=str)

    def load(self, path: str = None):
        p = path or self._path
        if p:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    self._events = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                self._events = []
