"""
core/db_migration_runner.py - versioned, ordered, fail-loud schema migration runner.

Before this module, init_db() ran an unconditional list of ~40 self-guarded _migrate_*
calls on every startup; each swallowed its own exceptions, so a genuine migration
failure never surfaced and nothing recorded what had been applied.

This module adds, without changing any individual migration:
  * a ``schema_migrations`` version table (version TEXT PRIMARY KEY, applied_at TEXT),
  * an ORDERED ``MIGRATIONS`` registry mirroring init_db()'s post-create sequence, and
  * ``run_migrations()`` which applies only not-yet-recorded migrations and FAILS LOUD
    (re-raises) at the runner boundary if a migration raises.

The registered ``_migrate_*`` functions are unchanged - each remains individually
idempotent and self-guarded, so running one when its version is unrecorded is safe even
on an already-migrated DB (it detects the column/table exists and no-ops). The runner
only adds version bookkeeping and the fail-loud boundary.

Import direction is one-way to avoid a cycle: this module imports the _migrate_*
callables + engine/utcnow_naive from core.database; core.database imports run_migrations
only via a deferred, function-local import inside init_db().
"""

import logging

from sqlalchemy import text

from core.database import (
    engine,
    utcnow_naive,
    _migrate_add_hidden_models_column,
    _migrate_add_cached_models_column,
    _migrate_add_pinned_models_column,
    _migrate_add_notes_sort_order,
    _migrate_add_model_type_column,
    _migrate_add_model_endpoint_refresh_columns,
    _migrate_add_model_endpoint_owner_column,
    _migrate_add_provider_auth_id_column,
    _migrate_add_supports_tools_column,
    _migrate_add_task_run_model_column,
    _migrate_add_owner_column,
    _migrate_add_document_archived_column,
    _migrate_add_last_message_at_column,
    _migrate_add_folder_column,
    _migrate_add_token_columns,
    _migrate_add_mode_column,
    _migrate_add_multiuser_owner_columns,
    _migrate_add_api_token_scopes_column,
    _migrate_backfill_document_owner_from_session,
    _migrate_assign_legacy_owner,
    _migrate_add_tidy_verdict,
    _migrate_add_doc_source_email_cols,
    _migrate_add_oauth_config,
    _migrate_add_task_automation_columns,
    _migrate_add_disabled_tools,
    _migrate_add_mcp_oauth_tokens_column,
    _migrate_add_task_v2_columns,
    _migrate_add_notifications_enabled,
    _migrate_drop_ping_notes_tasks,
    _migrate_add_crew_member_id,
    _migrate_add_assistant_columns,
    _migrate_add_email_smtp_security,
    _migrate_seed_email_account,
    _migrate_add_calendar_metadata,
    _migrate_add_calendar_is_utc,
    _migrate_add_calendar_origin,
    _migrate_add_calendar_account_id,
    _migrate_add_caldav_sync_columns,
    _migrate_chat_messages_fts,
    _migrate_encrypt_email_passwords,
    _migrate_encrypt_signatures,
    _migrate_encrypt_endpoint_keys,
    _migrate_backfill_task_folders,
)

logger = logging.getLogger(__name__)

# ORDERED registry mirroring init_db()'s post-create _migrate_* sequence EXACTLY.
# _migrate_model_endpoints runs PRE-create (it stays a pre-step in init_db) and is
# intentionally excluded. version_id format: "NNN_<func_name_without_leading_underscore>",
# NNN a zero-padded 3-digit ordinal matching init_db order.
_ORDERED_FUNCS = [
    _migrate_add_hidden_models_column,
    _migrate_add_cached_models_column,
    _migrate_add_pinned_models_column,
    _migrate_add_notes_sort_order,
    _migrate_add_model_type_column,
    _migrate_add_model_endpoint_refresh_columns,
    _migrate_add_model_endpoint_owner_column,
    _migrate_add_provider_auth_id_column,
    _migrate_add_supports_tools_column,
    _migrate_add_task_run_model_column,
    _migrate_add_owner_column,
    _migrate_add_document_archived_column,
    _migrate_add_last_message_at_column,
    _migrate_add_folder_column,
    _migrate_add_token_columns,
    _migrate_add_mode_column,
    _migrate_add_multiuser_owner_columns,
    _migrate_add_api_token_scopes_column,
    _migrate_backfill_document_owner_from_session,
    _migrate_assign_legacy_owner,
    _migrate_add_tidy_verdict,
    _migrate_add_doc_source_email_cols,
    _migrate_add_oauth_config,
    _migrate_add_task_automation_columns,
    _migrate_add_disabled_tools,
    _migrate_add_mcp_oauth_tokens_column,
    _migrate_add_task_v2_columns,
    _migrate_add_notifications_enabled,
    _migrate_drop_ping_notes_tasks,
    _migrate_add_crew_member_id,
    _migrate_add_assistant_columns,
    _migrate_add_email_smtp_security,
    _migrate_seed_email_account,
    _migrate_add_calendar_metadata,
    _migrate_add_calendar_is_utc,
    _migrate_add_calendar_origin,
    _migrate_add_calendar_account_id,
    _migrate_add_caldav_sync_columns,
    _migrate_chat_messages_fts,
    _migrate_encrypt_email_passwords,
    _migrate_encrypt_signatures,
    _migrate_encrypt_endpoint_keys,
    _migrate_backfill_task_folders,
]

MIGRATIONS = [
    (f"{i:03d}_{fn.__name__.lstrip('_')}", fn)
    for i, fn in enumerate(_ORDERED_FUNCS, start=1)
]


def _ensure_version_table(conn):
    """Create the schema_migrations version table if it does not exist."""
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version TEXT PRIMARY KEY, "
        "applied_at TEXT NOT NULL)"
    ))


def _applied_versions(conn):
    """Return the set of already-applied migration version ids."""
    rows = conn.execute(text("SELECT version FROM schema_migrations"))
    return {r[0] for r in rows}


def _record_applied(conn, version):
    """Record a migration version as applied (idempotent via INSERT OR IGNORE)."""
    conn.execute(
        text(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) "
            "VALUES (:v, :t)"
        ),
        {"v": version, "t": utcnow_naive().isoformat()},
    )


def run_migrations():
    """Apply all unrecorded migrations in order, recording each, failing loud on error.

    Each successfully-applied migration's version is recorded DURABLY (in its own short,
    committed transaction) BEFORE the next migration runs, so a later failure cannot
    un-record earlier successes. The registered _migrate_* callables each commit their
    schema change on their own connection; buffering the version row in a single
    loop-spanning transaction would let a later failure roll back bookkeeping for
    schema changes that had already physically committed, re-running them next startup.
    Holding no write transaction open across the whole loop also avoids the
    SQLITE_BUSY / "database is locked" risk under the default-pool engine.

    Each registered _migrate_* manages its own engine/connection internally and takes no
    args. Any exception escaping a migration is RE-RAISED (fail-loud) - we deliberately do
    NOT wrap fn() in a swallowing try/except. Already-applied versions are skipped.
    """
    with engine.begin() as conn:
        _ensure_version_table(conn)
        applied = _applied_versions(conn)

    for version, fn in MIGRATIONS:
        if version in applied:
            logger.info("run_migrations: skipping already-applied %s", version)
            continue
        logger.info("run_migrations: applying %s", version)
        fn()  # fail-loud: any exception propagates out of run_migrations
        with engine.begin() as conn:
            _record_applied(conn, version)
