import json
import os
import uuid
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

EVENTS_FILE = os.path.join("data", "homelab_events.json")

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _add_action_hints(event: dict) -> dict:
    if "suggested_actions" not in event:
        event["suggested_actions"] = ["ack", "investigate", "resolve", "ignore", "view_service"]
    return event

class EventStore:
    def __init__(self, file_path: str = EVENTS_FILE):
        self.file_path = file_path

    def _load(self) -> list:
        if not os.path.exists(self.file_path):
            return []
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f).get("events", [])
        except Exception as e:
            logger.error(f"Failed to load events: {e}")
            return []

    def _save(self, events: list):
        # Atomic write
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        tmp_path = self.file_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"events": events}, f, indent=2)
            os.replace(tmp_path, self.file_path)
        except Exception as e:
            logger.error(f"Failed to save events: {e}")
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            raise IOError(f"Persistence failure: {e}")

    def get_events(self, status: str = None, limit: int = None) -> list:
        events = self._load()
        
        if status == "open":
            events = [e for e in events if e.get("status") in ("new", "acknowledged", "investigating")]
        elif status:
            events = [e for e in events if e.get("status") == status]
            
        events.sort(key=lambda x: x.get("last_seen", ""), reverse=True)
        
        if limit is not None:
            events = events[:limit]
            
        return [_add_action_hints(e) for e in events]

    def get_event(self, event_id: str) -> dict:
        events = self._load()
        for e in events:
            if e["id"] == event_id:
                return _add_action_hints(e)
        return None

    def record_event(self, source: str, service: str, severity: str, title: str, summary: str, dedupe_key: str, owner: str = None, metadata: dict = None, suggested_actions: list[str] | None = None) -> dict:
        events = self._load()
        
        # Check for open event with same dedupe_key
        for e in events:
            if e.get("dedupe_key") == dedupe_key and e.get("status") not in ("resolved", "ignored"):
                e["count"] = e.get("count", 1) + 1
                e["last_seen"] = now_iso()
                
                # Only add a timeline entry if it's been a while, or just add it.
                e.setdefault("timeline", []).append({
                    "timestamp": now_iso(),
                    "action": "repeated",
                    "details": "Event deduplicated"
                })
                self._save(events)
                return _add_action_hints(e)

        # Create new
        new_event = {
            "id": str(uuid.uuid4()),
            "source": source,
            "service": service,
            "severity": severity,
            "status": "new",
            "title": title,
            "summary": summary,
            "dedupe_key": dedupe_key,
            "first_seen": now_iso(),
            "last_seen": now_iso(),
            "count": 1,
            "owner": owner,
            "metadata": metadata or {},
            "timeline": [{
                "timestamp": now_iso(),
                "action": "created",
                "details": "Event created"
            }]
        }
        if suggested_actions is not None:
            new_event["suggested_actions"] = suggested_actions
            
        events.append(new_event)
        self._save(events)
        return _add_action_hints(new_event)

    def update_status(self, event_id: str, status: str, user: str = None) -> dict:
        events = self._load()
        for e in events:
            if e["id"] == event_id:
                e["status"] = status
                e.setdefault("timeline", []).append({
                    "timestamp": now_iso(),
                    "action": status,
                    "details": f"Status updated to {status} by {user or 'system'}"
                })
                self._save(events)
                return _add_action_hints(e)
        return None
