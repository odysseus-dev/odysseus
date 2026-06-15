"""Unit coverage for core.db_migration_runner (MIG-P9-01).

Drives the runner against an isolated in-memory sqlite engine (StaticPool so a single
connection is reused, giving cross-statement persistence) and never touches the real
app DB. The order/idempotent/failloud tests monkeypatch the runner's MIGRATIONS and
engine; the registry-parity test parses init_db's source so the registry can never
silently drift from init_db's post-create sequence.
"""

import inspect

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

import core.db_migration_runner as runner
import core.database as migrations


def _mem_engine():
    # StaticPool keeps one underlying connection alive so the in-memory DB persists
    # across separate engine.begin()/engine.connect() blocks within a test.
    return create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_ensure_version_table_creates_schema_migrations():
    eng = _mem_engine()
    with eng.begin() as conn:
        runner._ensure_version_table(conn)
        rows = list(conn.execute(text("SELECT version FROM schema_migrations")))
    assert rows == []


def test_record_and_read_applied_versions():
    eng = _mem_engine()
    with eng.begin() as conn:
        runner._ensure_version_table(conn)
        runner._record_applied(conn, "001_alpha")
        runner._record_applied(conn, "002_beta")
        # duplicate is a no-op (INSERT OR IGNORE)
        runner._record_applied(conn, "001_alpha")
        applied = runner._applied_versions(conn)
    assert applied == {"001_alpha", "002_beta"}


def test_run_migrations_runs_in_order_and_records(monkeypatch):
    eng = _mem_engine()
    calls = []
    fake = [
        ("001_a", lambda: calls.append("001_a")),
        ("002_b", lambda: calls.append("002_b")),
        ("003_c", lambda: calls.append("003_c")),
    ]
    monkeypatch.setattr(runner, "engine", eng)
    monkeypatch.setattr(runner, "MIGRATIONS", fake)

    runner.run_migrations()

    assert calls == ["001_a", "002_b", "003_c"]
    with eng.begin() as conn:
        applied = runner._applied_versions(conn)
    assert applied == {"001_a", "002_b", "003_c"}


def test_run_migrations_idempotent_skips_applied(monkeypatch):
    eng = _mem_engine()
    calls = []
    fake = [
        ("001_a", lambda: calls.append("001_a")),
        ("002_b", lambda: calls.append("002_b")),
        ("003_c", lambda: calls.append("003_c")),
    ]
    # Pre-record 001 so it must be skipped on the run below.
    with eng.begin() as conn:
        runner._ensure_version_table(conn)
        runner._record_applied(conn, "001_a")

    monkeypatch.setattr(runner, "engine", eng)
    monkeypatch.setattr(runner, "MIGRATIONS", fake)

    runner.run_migrations()

    assert "001_a" not in calls
    assert calls == ["002_b", "003_c"]
    with eng.begin() as conn:
        applied = runner._applied_versions(conn)
    assert applied == {"001_a", "002_b", "003_c"}


def test_run_migrations_failloud_keeps_prefailure_versions_recorded(monkeypatch):
    # Self-committing fake migrations: each mirrors a real _migrate_* by opening its
    # OWN connection and committing its schema change independently of the runner's
    # version bookkeeping (real _migrate_* do engine.connect()+conn.commit()). This
    # exercises the schema-committed-then-record path, so the failloud invariant is
    # tested against the actual durability behaviour rather than a no-op lambda.
    eng = _mem_engine()

    def _self_committing(name):
        def fn():
            with eng.begin() as conn:
                conn.execute(
                    text("CREATE TABLE IF NOT EXISTS mig_marks (name TEXT PRIMARY KEY)")
                )
                conn.execute(
                    text("INSERT OR IGNORE INTO mig_marks(name) VALUES (:n)"),
                    {"n": name},
                )
        return fn

    def boom():
        raise RuntimeError("boom")

    fake = [
        ("001_a", _self_committing("001_a")),
        ("002_b", _self_committing("002_b")),
        ("003_c", boom),
    ]
    monkeypatch.setattr(runner, "engine", eng)
    monkeypatch.setattr(runner, "MIGRATIONS", fake)

    with pytest.raises(RuntimeError, match="boom"):
        runner.run_migrations()

    # CORRECT invariant (DI-P9-01): 001 and 002 committed their schema change AND had
    # their version durably recorded BEFORE 003 raised, so the fail-loud re-raise must
    # NOT un-record them. 003 raised before any schema change, so it is not recorded.
    with eng.begin() as conn:
        applied = runner._applied_versions(conn)
        marks = {r[0] for r in conn.execute(text("SELECT name FROM mig_marks"))}
    assert applied == {"001_a", "002_b"}
    assert "003_c" not in applied
    # The successfully-applied migrations' schema changes are durable too.
    assert marks == {"001_a", "002_b"}


# Canonical post-create migration ordering. This is the EXACT sequence init_db()
# enumerated before MIG-P9-02 delegated it to the runner (dropping the pre-create
# _migrate_model_endpoints), plus upstream additions registered in init_db order
# (_migrate_add_provider_auth_id_column, upstream/dev). Pinned here so the registry
# can never silently drift: any reorder/add/drop in the runner registry must be
# reflected in this list intentionally.
EXPECTED_POST_CREATE_ORDER = [
    "_migrate_add_hidden_models_column",
    "_migrate_add_cached_models_column",
    "_migrate_add_pinned_models_column",
    "_migrate_add_notes_sort_order",
    "_migrate_add_model_type_column",
    "_migrate_add_model_endpoint_refresh_columns",
    "_migrate_add_model_endpoint_owner_column",
    "_migrate_add_provider_auth_id_column",
    "_migrate_add_supports_tools_column",
    "_migrate_add_task_run_model_column",
    "_migrate_add_owner_column",
    "_migrate_add_document_archived_column",
    "_migrate_add_last_message_at_column",
    "_migrate_add_folder_column",
    "_migrate_add_token_columns",
    "_migrate_add_mode_column",
    "_migrate_add_multiuser_owner_columns",
    "_migrate_add_api_token_scopes_column",
    "_migrate_backfill_document_owner_from_session",
    "_migrate_assign_legacy_owner",
    "_migrate_add_tidy_verdict",
    "_migrate_add_doc_source_email_cols",
    "_migrate_add_oauth_config",
    "_migrate_add_task_automation_columns",
    "_migrate_add_disabled_tools",
    "_migrate_add_mcp_oauth_tokens_column",
    "_migrate_add_task_v2_columns",
    "_migrate_add_notifications_enabled",
    "_migrate_drop_ping_notes_tasks",
    "_migrate_add_crew_member_id",
    "_migrate_add_assistant_columns",
    "_migrate_add_email_smtp_security",
    "_migrate_seed_email_account",
    "_migrate_add_calendar_metadata",
    "_migrate_add_calendar_is_utc",
    "_migrate_add_calendar_origin",
    "_migrate_add_calendar_account_id",
    "_migrate_add_caldav_sync_columns",
    "_migrate_chat_messages_fts",
    "_migrate_encrypt_email_passwords",
    "_migrate_encrypt_signatures",
    "_migrate_encrypt_endpoint_keys",
    "_migrate_backfill_task_folders",
]


def test_registry_matches_init_db_order():
    # init_db() delegates the post-create sequence to run_migrations() (MIG-P9-02), so
    # the runner registry IS the canonical ordering. Guard it against drift by pinning
    # the expected order, and confirm init_db still routes through run_migrations and
    # keeps _migrate_model_endpoints as its pre-create step.
    registry_funcs = [fn.__name__ for _ver, fn in runner.MIGRATIONS]
    assert registry_funcs == EXPECTED_POST_CREATE_ORDER

    init_src = inspect.getsource(migrations.init_db)
    assert "_migrate_model_endpoints()" in init_src  # pre-create step retained
    assert "run_migrations()" in init_src             # post-create delegated to runner
    # _migrate_model_endpoints must NOT be in the runner registry (it is pre-create).
    assert "_migrate_model_endpoints" not in registry_funcs

    # version ids are zero-padded ordinals matching order
    versions = [ver for ver, _fn in runner.MIGRATIONS]
    assert versions[0].startswith("001_")
    assert versions == sorted(versions)
