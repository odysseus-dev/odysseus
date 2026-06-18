"""Durable lifecycle records for detached chat/agent streams."""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional

from core.database import AgentRunRecord, SessionLocal, utcnow_naive

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"done", "error", "stopped", "lost_after_restart"}


def begin(
    session_id: str,
    *,
    mode: str = "",
    model: str = "",
    requested_model: str = "",
    workspace_path: str = "",
    workspace_label: str = "",
    owner: Optional[str] = None,
    user_message_id: str = "",
) -> str:
    """Create a durable row for a detached run and return its id."""
    if not session_id:
        return ""
    run_id = uuid.uuid4().hex
    now = utcnow_naive()
    db = SessionLocal()
    try:
        db.add(AgentRunRecord(
            id=run_id,
            session_id=session_id,
            user_message_id=user_message_id or None,
            owner=owner,
            status="running",
            mode=mode or None,
            model=model or None,
            requested_model=requested_model or None,
            workspace_path=workspace_path or None,
            workspace_label=workspace_label or None,
            started_at=now,
            updated_at=now,
        ))
        db.commit()
        return run_id
    except Exception:
        db.rollback()
        logger.exception("Failed to create agent run record for session %s", session_id)
        return ""
    finally:
        db.close()


def finish(
    run_id: str,
    *,
    status: str,
    stop_reason: str = "",
    error: str = "",
    event_count: Optional[int] = None,
    partial_chars: Optional[int] = None,
    assistant_message_id: str = "",
    last_event_type: str = "",
) -> bool:
    """Mark a durable run row terminal."""
    if not run_id:
        return False
    db = SessionLocal()
    try:
        run = db.query(AgentRunRecord).filter(AgentRunRecord.id == run_id).first()
        if not run:
            return False
        now = utcnow_naive()
        run.status = status or run.status or "stopped"
        run.updated_at = now
        if run.status in TERMINAL_STATUSES and run.finished_at is None:
            run.finished_at = now
        if stop_reason:
            run.stop_reason = stop_reason
        if error:
            run.error = error[:2000]
        if event_count is not None:
            run.event_count = int(event_count)
        if partial_chars is not None:
            run.partial_chars = int(partial_chars)
        if assistant_message_id:
            run.assistant_message_id = assistant_message_id
        if last_event_type:
            run.last_event_type = last_event_type
        db.commit()
        return True
    except Exception:
        db.rollback()
        logger.exception("Failed to finish agent run record %s", run_id)
        return False
    finally:
        db.close()


def mark_lost_running_runs() -> int:
    """Mark rows left running by a previous server process as lost."""
    db = SessionLocal()
    try:
        stale = db.query(AgentRunRecord).filter(AgentRunRecord.status == "running").all()
        if not stale:
            return 0
        now = utcnow_naive()
        for run in stale:
            run.status = "lost_after_restart"
            run.stop_reason = "server_restart"
            run.error = "Server restarted before this detached run reported completion."
            run.updated_at = now
            run.finished_at = now
        db.commit()
        return len(stale)
    except Exception:
        db.rollback()
        logger.exception("Failed to mark stale agent run records")
        return 0
    finally:
        db.close()


def latest_for_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Return the newest durable run record for a session as a small dict."""
    if not session_id:
        return None
    db = SessionLocal()
    try:
        run = (
            db.query(AgentRunRecord)
            .filter(AgentRunRecord.session_id == session_id)
            .order_by(AgentRunRecord.started_at.desc())
            .first()
        )
        if not run:
            return None
        return {
            "id": run.id,
            "session_id": run.session_id,
            "status": run.status,
            "mode": run.mode,
            "model": run.model,
            "requested_model": run.requested_model,
            "workspace_path": run.workspace_path,
            "workspace_label": run.workspace_label,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "stop_reason": run.stop_reason,
            "error": run.error,
            "event_count": run.event_count or 0,
            "partial_chars": run.partial_chars or 0,
            "last_event_type": run.last_event_type,
        }
    except Exception:
        logger.exception("Failed to read latest agent run record for session %s", session_id)
        return None
    finally:
        db.close()
