import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("mdm.audit")


class AuditEntry:
    def __init__(self, agent: str, action: str, payload: dict, user: Optional[str] = None):
        self.agent = agent
        self.action = action
        self.payload = payload
        self.user = user
        self.timestamp = datetime.now(timezone.utc)
        self.duration_ms: Optional[float] = None

    def complete(self, duration_ms: float):
        self.duration_ms = duration_ms

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "action": self.action,
            "user": self.user,
            "timestamp": self.timestamp.isoformat(),
            "duration_ms": self.duration_ms,
        }


class AuditMiddleware:
    _log: list[AuditEntry] = []
    _max_entries = 1000

    @classmethod
    async def log(cls, agent: str, action: str, payload: dict, user: Optional[str] = None) -> AuditEntry:
        entry = AuditEntry(agent, action, payload, user)
        cls._log.append(entry)
        if len(cls._log) > cls._max_entries:
            cls._log.pop(0)
        return entry

    @classmethod
    def get_recent(cls, limit: int = 50) -> list[dict]:
        return [e.to_dict() for e in cls._log[-limit:]]

    @classmethod
    def get_stats(cls) -> dict:
        total = len(cls._log)
        by_agent = {}
        for e in cls._log:
            by_agent.setdefault(e.agent, 0)
            by_agent[e.agent] += 1
        return {"total": total, "by_agent": by_agent}
