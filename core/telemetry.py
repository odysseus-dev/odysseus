"""Telemetry metrics compiler for the /metrics in-chat command.

Queries the backend database and returns results as structured JSON
for beautiful rendering inside the chat UI. No external metric-tracking
packages are used — all values are derived from raw SQLAlchemy queries
against the application database.
"""

import logging

from sqlalchemy import func

from core.database import get_db_session, Session, ChatMessage, Document

logger = logging.getLogger(__name__)


def compile_metrics() -> dict:
    """Query backend telemetry and return a structured dict ready for
    JSON serialization and beautiful frontend rendering.

    Returns a dict with metric categories, values, and labels.
    """
    result: dict = {
        "active_sessions": 0,
        "chat_messages_by_model": [],
        "chat_messages_total": 0,
        "database_operations": [],
        "database_operations_total": 0,
    }

    try:
        with get_db_session() as db:
            # ── Active sessions ──
            active_count = (
                db.query(Session)
                .filter(Session.archived == False)
                .count()
            )
            result["active_sessions"] = active_count

            # ── Chat messages by model ──
            rows = (
                db.query(Session.model, func.count(ChatMessage.id))
                .join(ChatMessage, ChatMessage.session_id == Session.id)
                .group_by(Session.model)
                .all()
            )
            total_messages = 0
            for model, count in rows:
                label_model = (model or "").strip() or "unknown"
                result["chat_messages_by_model"].append({
                    "model": label_model,
                    "count": count,
                })
                total_messages += count
            result["chat_messages_total"] = total_messages

            # ── Database operations ──
            session_ops = db.query(Session).count()
            message_ops = db.query(ChatMessage).count()
            document_ops = (
                db.query(Document)
                .filter(Document.is_active == True)
                .count()
            )
            result["database_operations"] = [
                {"operation": "session",  "status": "success", "count": session_ops},
                {"operation": "message",  "status": "success", "count": message_ops},
                {"operation": "document", "status": "success", "count": document_ops},
            ]
            result["database_operations_total"] = session_ops + message_ops + document_ops

    except Exception:
        logger.exception("Failed to compile telemetry metrics")
        return {"error": "unable to query backend telemetry"}

    return result