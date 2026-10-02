# services/laya/audit.py
"""Persist laya runs to the LayaRun table. Best-effort: never raises.

Auditing must not be able to break a request, so every write is wrapped and
failures are logged, not propagated. Reads (for the admin screen in M3) return
plain dicts.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from .decisions import LayaDecision
from .policy import redact

logger = logging.getLogger(__name__)


def record(decision: LayaDecision, *, input_text: str, owner: Optional[str]) -> None:
    """Write one audit row for a completed decision. Swallows all errors."""
    try:
        from core.database import get_db_session, LayaRun

        preview, digest, length = redact(input_text)
        row = LayaRun(
            id=uuid.uuid4().hex,
            capability=decision.capability,
            owner=owner,
            shadow=decision.shadow,
            acted=decision.acted,
            reachable=decision.reachable,
            model=decision.model,
            decision=json.dumps(decision.to_dict(), ensure_ascii=False),
            answer_confidence=decision.confidence,
            input_sha256=digest,
            input_preview=preview,
            input_len=length,
            latency_ms=decision.latency_ms,
            error=decision.error,
        )
        with get_db_session() as db:
            db.add(row)
    except Exception:
        logger.exception("laya audit write failed (non-fatal)")


def recent(limit: int = 50, capability: Optional[str] = None) -> List[Dict[str, Any]]:
    """Most-recent runs, newest first. Returns [] on any error."""
    try:
        from core.database import get_db_session, LayaRun

        with get_db_session() as db:
            q = db.query(LayaRun)
            if capability:
                q = q.filter(LayaRun.capability == capability)
            rows = q.order_by(LayaRun.created_at.desc()).limit(max(1, min(limit, 500))).all()
            return [
                {
                    "id": r.id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "capability": r.capability,
                    "owner": r.owner,
                    "shadow": r.shadow,
                    "acted": r.acted,
                    "reachable": r.reachable,
                    "model": r.model,
                    "answer_confidence": r.answer_confidence,
                    "input_preview": r.input_preview,
                    "input_len": r.input_len,
                    "latency_ms": r.latency_ms,
                    "error": r.error,
                }
                for r in rows
            ]
    except Exception:
        logger.exception("laya audit read failed (non-fatal)")
        return []


def summary() -> Dict[str, Any]:
    """Aggregate counters for the admin screen. Returns a safe default on error."""
    out = {"total": 0, "reachable": 0, "errors": 0, "acted": 0, "by_capability": {}, "last_run": None}
    try:
        from core.database import get_db_session, LayaRun

        with get_db_session() as db:
            rows = db.query(LayaRun).all()
            out["total"] = len(rows)
            out["reachable"] = sum(1 for r in rows if r.reachable)
            out["errors"] = sum(1 for r in rows if r.error)
            out["acted"] = sum(1 for r in rows if r.acted)
            caps: Dict[str, int] = {}
            last = None
            for r in rows:
                caps[r.capability] = caps.get(r.capability, 0) + 1
                if r.created_at and (last is None or r.created_at > last):
                    last = r.created_at
            out["by_capability"] = caps
            out["last_run"] = last.isoformat() if last else None
    except Exception:
        logger.exception("laya audit summary failed (non-fatal)")
    return out
