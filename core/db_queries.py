"""
core/db_queries.py â€” runtime session / query helpers.

Extracted from core/database.py (P8-T10): get_db / get_db_session and the
session-stat / bulk-insert / mode / upcoming-events helpers. Imports
SessionLocal and the model classes it needs from core.db_models. core/database.py
re-exports these so existing importers keep resolving them off core.database.
"""

import os
from contextlib import contextmanager
from typing import Generator

from core.db_models import (
    SessionLocal, DATABASE_URL, logger, utcnow_naive,
    Session, ChatMessage, Memory, CalendarEvent, CalendarCal,
)

def get_db():
    """
    Dependency to get a database session.
    Used in FastAPI routes to inject database sessions.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()



def get_db_session(session_factory=None):
    """Moved to core.database (its canonical home); lazy passthrough avoids a cycle."""
    from core.database import get_db_session as _gds
    return _gds(session_factory)

def bulk_insert_messages(session_id: str, messages: list):
    """Efficiently insert multiple messages"""
    with get_db_session() as db:
        db.bulk_insert_mappings(
            ChatMessage,
            [
                {
                    'session_id': session_id,
                    'role': msg['role'],
                    'content': msg['content'],
                    'timestamp': utcnow_naive()
                }
                for msg in messages
            ]
        )

def cleanup_old_sessions(days: int = 30):
    """Remove sessions older than specified days"""
    from datetime import timedelta
    
    with get_db_session() as db:
        cutoff_date = utcnow_naive() - timedelta(days=days)
        
        deleted_count = db.query(Session).filter(
            Session.archived == True,
            Session.last_accessed < cutoff_date,
            Session.is_important == False
        ).delete()
        
        return deleted_count

def get_session_stats():
    """Get database statistics"""
    with get_db_session() as db:
        stats = {
            'total_sessions': db.query(Session).count(),
            'active_sessions': db.query(Session).filter(Session.archived == False).count(),
            'archived_sessions': db.query(Session).filter(Session.archived == True).count(),
            'total_messages': db.query(ChatMessage).count(),
            'total_memories': db.query(Memory).count()
        }
        return stats

def get_detailed_stats():
    """Get comprehensive database statistics including file size"""
    stats = get_session_stats()  # Use existing function
    
    # Add database file size
    db_size_mb = 0.0
    if "sqlite" in DATABASE_URL:
        db_path = DATABASE_URL.replace("sqlite:///", "")
        if not os.path.isabs(db_path):
            db_path = os.path.abspath(db_path)
        
        if os.path.exists(db_path):
            db_size = os.path.getsize(db_path)
            db_size_mb = round(db_size / (1024 * 1024), 2)
    
    stats['database_size_mb'] = db_size_mb
    return stats

def update_session_last_accessed(session_id: str):
    """Update the last_accessed timestamp for a session"""
    with get_db_session() as db:
        db_session = db.query(Session).filter(Session.id == session_id).first()
        if db_session:
            db_session.last_accessed = utcnow_naive()
            db.commit()
            return True
    return False



def get_session_by_id(session_id: str):
    """Get a session by ID"""
    with get_db_session() as db:
        return db.query(Session).filter(Session.id == session_id).first()


def archive_session(session_id: str):
    """Archive a session"""
    with get_db_session() as db:
        session = db.query(Session).filter(Session.id == session_id).first()
        if session:
            session.archived = True
            db.commit()
            return True
    return False
