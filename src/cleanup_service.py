# src/cleanup_service.py
import logging
from datetime import datetime, timedelta, timezone
from typing import Tuple, Dict, Any, Optional

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Naive UTC for this module's DB-bound timestamps.

    Mirrors the naive DateTime columns these values are compared against,
    without the deprecated stdlib UTC-now call (removed in Python 3.14).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class CleanupConfig:
    """Configuration constants for cleanup operations."""
    ARCHIVE_AFTER_DAYS = 7
    DELETE_AFTER_DAYS = 14
    MIN_MESSAGES_TO_KEEP = 20
    PRESERVE_RECENT_COUNT = 10
    PROTECTED_KEYWORDS = ['important', 'remember', 'save this', 'keep', 'bookmark']
    ESTIMATED_MESSAGE_SIZE_BYTES = 512


def _apply_owner_filter(query, DbSession, owner: Optional[str]):
    """Apply owner filtering to a session query.

    SECURITY: strict — the previous OR predicate let one user's cleanup
    archive/delete every null-owner session, including ones that hadn't
    been migrated. Now: only rows owned by this user.
    """
    if owner is None:
        return query
    return query.filter(DbSession.owner == owner)


async def archive_inactive_sessions(session_manager, owner: Optional[str] = None) -> int:
    """
    Archive sessions that haven't been accessed in the configured number of days.

    Args:
        session_manager: The session manager instance
        owner: If set, only archive this user's sessions

    Returns:
        Number of sessions archived
    """
    cutoff_date = _utcnow() - timedelta(days=CleanupConfig.ARCHIVE_AFTER_DAYS)
    archived_count = 0

    from src.database import SessionLocal, Session as DbSession
    db = SessionLocal()
    try:
        q = db.query(DbSession).filter(
            DbSession.last_accessed < cutoff_date,
            DbSession.archived == False
        )
        q = _apply_owner_filter(q, DbSession, owner)
        sessions_to_archive = q.all()

        for session in sessions_to_archive:
            session.archived = True
            session.updated_at = _utcnow()
            archived_count += 1

        if archived_count > 0:
            db.commit()
            logger.info(f"Archived {archived_count} inactive sessions")

    except Exception as e:
        logger.error(f"Error archiving sessions: {e}")
        db.rollback()
    finally:
        db.close()

    return archived_count

async def cleanup_old_sessions(session_manager, owner: Optional[str] = None) -> Tuple[int, float]:
    """
    Delete old sessions based on specific criteria.

    Args:
        session_manager: The session manager instance
        owner: If set, only clean up this user's sessions

    Returns:
        Tuple of (number of sessions deleted, space freed in MB)
    """
    cutoff_date = _utcnow() - timedelta(days=CleanupConfig.DELETE_AFTER_DAYS)
    deleted_count = 0
    space_freed = 0

    from src.database import SessionLocal, Session as DbSession, ChatMessage as DbChatMessage
    db = SessionLocal()
    try:
        recent_q = db.query(DbSession).order_by(DbSession.created_at.desc())
        recent_q = _apply_owner_filter(recent_q, DbSession, owner)
        all_sessions = recent_q.all()
        recent_session_ids = {session.id for session in all_sessions[:CleanupConfig.PRESERVE_RECENT_COUNT]}

        base_query = db.query(DbSession).filter(
            DbSession.archived == True,
            DbSession.last_accessed < cutoff_date,
            DbSession.is_important == False,
            DbSession.message_count < CleanupConfig.MIN_MESSAGES_TO_KEEP
        )
        base_query = _apply_owner_filter(base_query, DbSession, owner)

        candidate_sessions = base_query.all()
        sessions_to_delete = []
        preserved_count = 0

        for session in candidate_sessions:
            if session.id in recent_session_ids:
                preserved_count += 1
                continue

            if session.message_count >= CleanupConfig.MIN_MESSAGES_TO_KEEP:
                preserved_count += 1
                continue

            session_name_lower = session.name.lower() if session.name else ""
            if any(keyword in session_name_lower for keyword in CleanupConfig.PROTECTED_KEYWORDS):
                preserved_count += 1
                continue

            sessions_to_delete.append(session)

        for session in sessions_to_delete:
            message_count = db.query(DbChatMessage).filter(
                DbChatMessage.session_id == session.id
            ).count()
            space_freed += message_count * CleanupConfig.ESTIMATED_MESSAGE_SIZE_BYTES

        session_ids = [session.id for session in sessions_to_delete]
        if session_ids:
            db.query(DbSession).filter(DbSession.id.in_(session_ids)).delete(synchronize_session=False)
            deleted_count = len(session_ids)
            db.commit()

            for session_id in session_ids:
                if session_id in session_manager.sessions:
                    del session_manager.sessions[session_id]

        if deleted_count > 0:
            space_freed_mb = space_freed / (1024 * 1024)
            logger.info(f"Deleted {deleted_count} old sessions, freeing approximately {space_freed_mb:.2f} MB")
            return deleted_count, space_freed_mb

    except Exception as e:
        logger.error(f"Error cleaning up old sessions: {e}")
        db.rollback()
    finally:
        db.close()

    return deleted_count, 0.0

async def get_cleanup_preview(owner: Optional[str] = None) -> Dict[str, Any]:
    """
    Get a preview of what would be cleaned up without making changes.

    Args:
        owner: If set, only preview this user's sessions

    Returns:
        Dictionary containing preview information
    """
    cutoff_archive = _utcnow() - timedelta(days=CleanupConfig.ARCHIVE_AFTER_DAYS)
    cutoff_delete = _utcnow() - timedelta(days=CleanupConfig.DELETE_AFTER_DAYS)

    sessions_to_archive = []
    sessions_to_delete = []
    estimated_space_freed = 0
    preserved_sessions = []

    from src.database import SessionLocal, Session as DbSession
    db = SessionLocal()
    try:
        archive_q = db.query(DbSession).filter(
            DbSession.last_accessed < cutoff_archive,
            DbSession.archived == False
        )
        archive_q = _apply_owner_filter(archive_q, DbSession, owner)
        archive_candidates = archive_q.all()

        for session in archive_candidates:
            sessions_to_archive.append({
                "id": session.id,
                "name": session.name,
                "last_accessed": session.last_accessed.isoformat() if session.last_accessed else "Unknown",
                "message_count": session.message_count
            })

        recent_q = db.query(DbSession).order_by(DbSession.created_at.desc())
        recent_q = _apply_owner_filter(recent_q, DbSession, owner)
        all_sessions = recent_q.all()
        recent_session_ids = {session.id for session in all_sessions[:CleanupConfig.PRESERVE_RECENT_COUNT]}

        base_query = db.query(DbSession).filter(
            DbSession.archived == True,
            DbSession.last_accessed < cutoff_delete,
            DbSession.is_important == False,
            DbSession.message_count < CleanupConfig.MIN_MESSAGES_TO_KEEP
        )
        base_query = _apply_owner_filter(base_query, DbSession, owner)

        candidate_sessions = base_query.all()

        for session in candidate_sessions:
            if session.id in recent_session_ids:
                preserved_sessions.append({
                    "id": session.id,
                    "name": session.name,
                    "reason": f"part of last {CleanupConfig.PRESERVE_RECENT_COUNT} sessions",
                    "last_accessed": session.last_accessed.isoformat() if session.last_accessed else "Unknown",
                    "message_count": session.message_count
                })
                continue

            if session.message_count >= CleanupConfig.MIN_MESSAGES_TO_KEEP:
                preserved_sessions.append({
                    "id": session.id,
                    "name": session.name,
                    "reason": f"has {CleanupConfig.MIN_MESSAGES_TO_KEEP}+ messages",
                    "last_accessed": session.last_accessed.isoformat() if session.last_accessed else "Unknown",
                    "message_count": session.message_count
                })
                continue

            session_name_lower = session.name.lower() if session.name else ""
            matching_keywords = [keyword for keyword in CleanupConfig.PROTECTED_KEYWORDS if keyword in session_name_lower]
            if matching_keywords:
                preserved_sessions.append({
                    "id": session.id,
                    "name": session.name,
                    "reason": f"contains keyword: {matching_keywords[0]}",
                    "last_accessed": session.last_accessed.isoformat() if session.last_accessed else "Unknown",
                    "message_count": session.message_count
                })
                continue

            session_space = session.message_count * CleanupConfig.ESTIMATED_MESSAGE_SIZE_BYTES
            estimated_space_freed += session_space

            sessions_to_delete.append({
                "id": session.id,
                "name": session.name,
                "last_accessed": session.last_accessed.isoformat() if session.last_accessed else "Unknown",
                "message_count": session.message_count,
                "estimated_size_kb": round(session_space / 1024, 2)
            })

    except Exception as e:
        logger.error(f"Error generating cleanup preview: {e}")
    finally:
        db.close()

    return {
        "sessions_to_archive": sessions_to_archive,
        "sessions_to_delete": sessions_to_delete,
        "preserved_sessions": preserved_sessions,
        "estimated_space_freed_mb": round(estimated_space_freed / (1024 * 1024), 2)
    }

async def cleanup_sessions(session_manager, owner: Optional[str] = None) -> Tuple[int, int, float]:
    """
    Perform complete cleanup operations with error recovery.

    Args:
        session_manager: The session manager instance
        owner: If set, only clean up this user's sessions

    Returns:
        Tuple of (archived_count, deleted_count, space_freed_mb)
    """
    archived_count = 0
    deleted_count = 0
    space_freed_mb = 0.0

    try:
        archived_count = await archive_inactive_sessions(session_manager, owner=owner)
    except Exception as e:
        logger.error(f"Archive operation failed: {e}")

    try:
        deleted_count, space_freed_mb = await cleanup_old_sessions(session_manager, owner=owner)
    except Exception as e:
        logger.error(f"Delete operation failed: {e}")

    return archived_count, deleted_count, space_freed_mb
