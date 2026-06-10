"""
core/db_migration_runner.py — versioned, ordered, fail-loud migration runner (MIG-P9-01).

This is the ADDITIVE schema-versioning layer for odysseus. Before P9 the post-create
migration sequence in core/db_migrations.py::init_db() was an unconditional list of ~40
self-guarded, internally-idempotent _migrate_* calls, each of which swallowed its own
exceptions (``except Exception: logger.warning(...)``) — so a genuine migration failure
never surfaced (DI-P8-01 at the orchestration level).

This module introduces:
  * a ``schema_migrations`` version table (version TEXT PRIMARY KEY, applied_at TEXT),
  * an ORDERED ``MIGRATIONS`` registry mirroring the EXACT post-create ordering of
    init_db(), and
  * ``run_migrations()`` which applies only the migrations whose version is not yet
    recorded and FAILS LOUD (re-raises) at the runner boundary if a migration raises.

Design notes:
  * The registered ``_migrate_*`` functions are unchanged — each stays individually
    idempotent and self-guarded, so calling one once when its version is unrecorded is
    safe even on an already-migrated DB (it detects the column/table already exists and
    no-ops). The runner adds the version bookkeeping and the fail-loud boundary on top;
    it does NOT make individual migrations destructive on re-run.
  * Import direction is one-way to avoid a cycle: this module imports from
    core.db_migrations (the _migrate_* callables) and core.db_models (engine, utcnow_naive);
    core.db_migrations imports only from core.db_models, never from this module.
    init_db() (T3) uses a deferred function-local import of run_migrations().
"""

import logging

from sqlalchemy import text

from core.db_models import engine, utcnow_naive
from core.db_migrations import (
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
    _migrate_chat_messages_fts,
    _migrate_encrypt_email_passwords,
    _migrate_encrypt_signatures,
    _migrate_encrypt_endpoint_keys,
    _migrate_backfill_task_folders,
)

logger = logging.getLogger(__name__)

# ORDERED registry mirroring init_db()'s post-create _migrate_* sequence EXACTLY
# (core/db_migrations.py init_db lines 866-906). _migrate_model_endpoints runs PRE-create
# (it stays a pre-step in init_db) and is intentionally excluded; _migrate_add_owner_to_table
# is a helper invoked by _migrate_add_multiuser_owner_columns and is not a top-level step.
# version_id format: "NNN_<func_name_without_leading_underscore>", NNN a zero-padded
# 3-digit ordinal matching init_db order.
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

    Each successfully-applied migration's version is recorded DURABLY (in its own
    short, committed transaction) BEFORE the next migration runs, so a later failure
    cannot un-record earlier successes (DI-P9-01). The registered _migrate_*
    callables each commit their schema change on their own independent connection;
    if the version row were buffered in a single loop-spanning outer transaction, a
    later migration's failure would roll back the bookkeeping for migrations whose
    schema changes had already physically committed, and those would re-run on the
    next startup. Holding no write transaction open across the whole loop also
    removes the SQLITE_BUSY / "database is locked" risk under the production
    default-pool engine (DI-P9-02).

    Each registered _migrate_* manages its own engine/connection internally and
    takes no args. Any exception escaping a migration is RE-RAISED (fail-loud) — we
    deliberately do NOT wrap fn() in a swallowing try/except. Already-applied
    versions are skipped (idempotent re-run).
    """
    # 1. Ensure the version table exists, committed up front in its own transaction.
    with engine.begin() as conn:
        _ensure_version_table(conn)
        applied = _applied_versions(conn)

    # 2. Apply each not-yet-applied migration, then DURABLY record its version in its
    #    own short committed transaction before moving to the next one. A failure in
    #    fn() propagates immediately (fail-loud) and leaves all prior version rows
    #    durably committed.
    for version, fn in MIGRATIONS:
        if version in applied:
            logger.info("run_migrations: skipping already-applied %s", version)
            continue
        logger.info("run_migrations: applying %s", version)
        fn()  # fail-loud: any exception propagates out of run_migrations
        with engine.begin() as conn:
            _record_applied(conn, version)
