import time
from typing import Dict, List


class MetricsStore:
    def __init__(self):
        self._raw: List[dict] = []

    def update(self, step: dict, result: dict):
        self._raw.append({
            "agent": step.get("agent"),
            "action": step.get("action"),
            "status": result.get("status", "unknown"),
            "error": result.get("error"),
            "duration_ms": result.get("duration_ms", 0),
            "timestamp": time.time(),
        })

    def summary(self) -> dict:
        if not self._raw:
            return {"total_runs": 0, "error_rate": 0, "avg_duration_ms": 0, "avg_tokens": 0, "by_agent": {}}

        total = len(self._raw)
        errors = [r for r in self._raw if r["status"] == "error"]
        by_agent: Dict[str, list] = {}
        for r in self._raw:
            by_agent.setdefault(r["agent"], []).append(r)

        return {
            "total_runs": total,
            "error_rate": round(len(errors) / total, 3),
            "total_errors": len(errors),
            "avg_duration_ms": round(sum(r["duration_ms"] for r in self._raw) / total, 1),
            "by_agent": {
                agent: {
                    "runs": len(rs),
                    "error_rate": round(sum(1 for r in rs if r["status"] == "error") / len(rs), 3),
                    "avg_duration_ms": round(sum(r["duration_ms"] for r in rs) / len(rs), 1),
                }
                for agent, rs in by_agent.items()
            },
        }

    def get_recent(self, n: int = 50) -> list:
        return self._raw[-n:]
