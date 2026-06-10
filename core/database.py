"""
core/database.py — aggregator / backward-compat shim.

The database layer was decomposed into cohesive submodules (P8-T8..T10):

    core/db_models.py       engine/SessionLocal/Base/mixins + all ORM models
    core/db_migrations.py   every _migrate_* function + init_db()
    core/db_queries.py      get_db/get_db_session + session-stat query helpers

This module now only re-exports those names so its ~89 importers
("from core.database import SessionLocal, Document, init_db, get_db_session, ...")
keep resolving unchanged. No models, migrations or query logic live here anymore.
"""

# ---------------------------------------------------------------------------
# ORM models + engine/SessionLocal moved to core/db_models.py (P8-T8).
# Re-exported here so the ~89 importers of core.database keep resolving every
# model class, Base, engine, SessionLocal, etc. unchanged. Migrations and
# query helpers below reference these re-exported names.
# ---------------------------------------------------------------------------
from core.db_models import *  # noqa: F401,F403
from core.db_models import (  # noqa: F401
    Base, TimestampMixin, EncryptedText, engine, SessionLocal, DATABASE_URL,
    utcnow_naive, set_sqlite_pragma,
    Session, ChatMessage, Document, DocumentVersion, GalleryAlbum, GalleryImage,
    EmailAccount, ModelEndpoint, ProviderAuthSession, McpServer, Comparison, Signature, ApiToken,
    Webhook, UserTool, UserToolData, CrewMember, ScheduledTask, EditorDraft,
    TaskRun, Memory, Note, CalendarCal, CalendarEvent, Integration,
)


# ---------------------------------------------------------------------------
# Migrations + init_db moved to core/db_migrations.py (P8-T9). Re-exported here
# so every importer keeps resolving init_db and the _migrate_* names off
# core.database. `import *` skips underscore names, so each _migrate_* is
# imported explicitly (tests reference e.g. core.database._migrate_chat_messages_fts).
# ---------------------------------------------------------------------------
from core.db_migrations import *  # noqa: F401,F403
from core.db_migrations import (  # noqa: F401
    init_db,
    _migrate_add_last_message_at_column,
    _migrate_add_document_archived_column,
    _migrate_add_owner_column,
    _migrate_model_endpoints,
    _migrate_add_hidden_models_column,
    _migrate_add_model_endpoint_owner_column,
    _migrate_add_provider_auth_id_column,
    _migrate_add_model_type_column,
    _migrate_add_model_endpoint_refresh_columns,
    _migrate_add_task_run_model_column,
    _migrate_add_supports_tools_column,
    _migrate_add_cached_models_column,
    _migrate_add_pinned_models_column,
    _migrate_add_notes_sort_order,
    _migrate_add_mode_column,
    _migrate_add_folder_column,
    _migrate_add_token_columns,
    _migrate_add_owner_to_table,
    _migrate_add_multiuser_owner_columns,
    _migrate_add_api_token_scopes_column,
    _migrate_assign_legacy_owner,
    _migrate_backfill_document_owner_from_session,
    _migrate_add_tidy_verdict,
    _migrate_add_doc_source_email_cols,
    _migrate_add_task_automation_columns,
    _migrate_add_oauth_config,
    _migrate_add_disabled_tools,
    _migrate_add_mcp_oauth_tokens_column,
    _migrate_add_task_v2_columns,
    _migrate_drop_ping_notes_tasks,
    _migrate_add_notifications_enabled,
    _migrate_add_crew_member_id,
    _migrate_add_assistant_columns,
    _migrate_seed_email_account,
    _migrate_backfill_task_folders,
    _migrate_chat_messages_fts,
    _migrate_add_email_smtp_security,
    _migrate_encrypt_endpoint_keys,
    _migrate_encrypt_signatures,
    _migrate_encrypt_email_passwords,
    _migrate_add_calendar_is_utc,
    _migrate_add_calendar_origin,
    _migrate_add_calendar_account_id,
    _migrate_add_calendar_metadata,
)

# ---------------------------------------------------------------------------
# Query / session helpers moved to core/db_queries.py (P8-T10). Re-exported so
# the ~89 importers of core.database keep resolving them unchanged. core.database
# is now a thin aggregator over db_models + db_migrations + db_queries.
# ---------------------------------------------------------------------------
from core.db_queries import *  # noqa: F401,F403
from core.db_queries import (  # noqa: F401
    get_db, bulk_insert_messages, cleanup_old_sessions,
    get_session_stats, get_detailed_stats, update_session_last_accessed,
    get_session_by_id, archive_session,
)
# get_db_session / get_session_mode / set_session_mode / get_upcoming_events are
# defined at the bottom of THIS module (tests AST-extract them from here).

# ---------------------------------------------------------------------------
# Session/context helpers. These live HERE (not in a submodule) deliberately:
# tests AST-extract their FunctionDefs from core/database.py to pin leak-safety
# and owner-scoping properties.
# ---------------------------------------------------------------------------
from contextlib import contextmanager
from typing import Generator

@contextmanager
def get_db_session(session_factory=None) -> Generator:
    """Context manager for database sessions"""
    session = (session_factory or SessionLocal)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session_mode(session_id: str):
    """Return a session's persisted `mode`, or None if unset/unknown.

    Best-effort: never raises (returns None on any DB error) so callers on hot
    request paths needn't guard it. Routed through get_db_session() so the
    connection is always returned to the pool."""
    try:
        with get_db_session() as db:
            return db.query(Session.mode).filter(Session.id == session_id).scalar()
    except Exception:
        logger.warning("Failed to read mode for session %s", session_id)
        return None


def set_session_mode(session_id: str, mode: str) -> bool:
    """Persist a session's `mode`. Best-effort: never raises, returns success.

    Routed through get_db_session() so a failure mid-write (e.g. a SQLite
    'database is locked' under concurrent streams) still returns the connection
    to the pool instead of leaking it â€” repeated leaks would exhaust it."""
    try:
        with get_db_session() as db:
            db.query(Session).filter(Session.id == session_id).update({"mode": mode})
        return True
    except Exception:
        logger.warning("Failed to persist mode %r for session %s", mode, session_id)
        return False


def get_upcoming_events(owner, horizon_days: int = 60, limit: int = 40):
    """Upcoming, non-cancelled events as {uid, title, start} dicts, soonest first.

    owner=None means NO owner scoping (single-user / legacy). Multi-user callers
    MUST pass the owning username â€” otherwise they read every tenant's events.
    The autonomous email->calendar pass relies on this to avoid disclosing (and
    acting on) other users' calendars."""
    from datetime import timedelta
    now = utcnow_naive()
    with get_db_session() as db:
        q = db.query(CalendarEvent).join(CalendarCal).filter(
            CalendarEvent.dtstart >= now,
            CalendarEvent.dtstart <= now + timedelta(days=horizon_days),
            CalendarEvent.status != "cancelled",
        )
        if owner is not None:
            q = q.filter(CalendarCal.owner == owner)
        return [
            {
                "uid": e.uid,
                "title": e.summary or "",
                "start": e.dtstart.isoformat() if e.dtstart else "",
            }
            for e in q.order_by(CalendarEvent.dtstart).limit(limit).all()
        ]
