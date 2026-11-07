"""Regressions for process-safe email-account default mutations.

The file-backed SQLite fixture uses a fresh connection for every Session.
That exercises the same database lock boundary used by separate web workers,
rather than relying on an in-process Python lock.
"""

import asyncio
import json
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, create_mock_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool


@pytest.fixture
def account_db(tmp_path, monkeypatch):
    from core import database as core_db

    engine = create_engine(
        f"sqlite:///{tmp_path / 'accounts.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
    )
    core_db.Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
    )
    monkeypatch.setattr(core_db, "SessionLocal", factory)
    yield factory
    engine.dispose()


def _endpoint(method, path):
    from routes import email_routes

    with mock.patch.object(email_routes, "_start_poller"):
        router = email_routes.setup_email_routes()
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"email route not found: {method} {path}")


def _named_endpoint(router, name):
    for route in router.routes:
        if getattr(getattr(route, "endpoint", None), "__name__", "") == name:
            return route.endpoint
    raise AssertionError(f"route not found: {name}")


def _seed_account(factory, account_id, owner, *, is_default=False, enabled=True):
    from core.database import EmailAccount

    db = factory()
    try:
        db.add(
            EmailAccount(
                id=account_id,
                owner=owner,
                name=account_id,
                is_default=is_default,
                enabled=enabled,
            )
        )
        db.commit()
    finally:
        db.close()


def _rows(factory):
    from core.database import EmailAccount

    db = factory()
    try:
        return [
            (row.id, row.owner, bool(row.is_default))
            for row in db.query(EmailAccount).order_by(EmailAccount.id).all()
        ]
    finally:
        db.close()


def _install_lock_pause(monkeypatch, paused_thread_name):
    """Pause one worker after acquisition and observe another waiting."""
    from routes import email_routes

    real_lock = email_routes._lock_email_account_owner_mutation
    first_acquired = threading.Event()
    release_first = threading.Event()
    contender_attempted = threading.Event()
    contender_acquired = threading.Event()

    def controlled_lock(db, owner):
        is_first = threading.current_thread().name == paused_thread_name
        if not is_first:
            contender_attempted.set()
        real_lock(db, owner)
        if is_first:
            first_acquired.set()
            assert release_first.wait(5), "timed out releasing first mutation"
        else:
            contender_acquired.set()

    monkeypatch.setattr(
        email_routes,
        "_lock_email_account_owner_mutation",
        controlled_lock,
    )
    return first_acquired, release_first, contender_attempted, contender_acquired


def test_concurrent_first_account_creates_choose_one_default(account_db, monkeypatch):
    create_account = _endpoint("POST", "/api/email/accounts")
    first_acquired, release_first, attempted, acquired = _install_lock_pause(
        monkeypatch, "first-account"
    )
    results = {}

    def create(name):
        results[name] = asyncio.run(
            create_account({"name": name, "is_default": False}, owner="alice")
        )

    first = threading.Thread(target=create, args=("First",), name="first-account")
    second = threading.Thread(target=create, args=("Second",), name="second-account")
    first.start()
    assert first_acquired.wait(5)
    second.start()
    assert attempted.wait(5)
    assert not acquired.wait(0.1), "second session bypassed the database mutation lock"

    release_first.set()
    first.join(5)
    second.join(5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results["First"]["ok"] is True
    assert results["Second"]["ok"] is True
    defaults = [row for row in _rows(account_db) if row[2]]
    assert [(row[1], row[2]) for row in defaults] == [("alice", True)]
    assert len(defaults) == 1


def test_delete_promotion_and_set_default_are_one_serial_transition(
    account_db, monkeypatch
):
    from sqlalchemy.orm import Session as OrmSession

    _seed_account(account_db, "alice-a", "alice", is_default=True)
    _seed_account(account_db, "alice-b", "alice")
    _seed_account(account_db, "alice-c", "alice")
    _seed_account(account_db, "bob-a", "bob", is_default=True)

    delete_account = _endpoint("DELETE", "/api/email/accounts/{account_id}")
    set_default = _endpoint("POST", "/api/email/accounts/{account_id}/set-default")
    first_acquired, release_first, attempted, acquired = _install_lock_pause(
        monkeypatch, "delete-default"
    )
    delete_commit_finished = threading.Event()
    release_delete_after_commit = threading.Event()
    real_commit = OrmSession.commit
    results = {}

    def pause_after_delete_commit(session):
        real_commit(session)
        if (
            threading.current_thread().name == "delete-default"
            and not delete_commit_finished.is_set()
        ):
            delete_commit_finished.set()
            assert release_delete_after_commit.wait(5), (
                "timed out releasing delete after its first commit"
            )

    monkeypatch.setattr(OrmSession, "commit", pause_after_delete_commit)

    def delete_old_default():
        results["delete"] = asyncio.run(
            delete_account("alice-a", owner="alice")
        )

    def select_new_default():
        results["set"] = asyncio.run(
            set_default("alice-c", owner="alice")
        )

    delete_thread = threading.Thread(target=delete_old_default, name="delete-default")
    set_thread = threading.Thread(target=select_new_default, name="set-default")
    delete_thread.start()
    assert first_acquired.wait(5)
    set_thread.start()
    assert attempted.wait(5)
    assert not acquired.wait(0.1), "set-default bypassed the delete transaction"

    release_first.set()
    assert delete_commit_finished.wait(5)
    # The deletion transaction has committed.  Let the contender complete
    # before the deleting handler can continue: if promotion were still a
    # second commit, it would now run after set-default and recreate two
    # defaults deterministically.
    assert acquired.wait(5)
    set_thread.join(5)
    release_delete_after_commit.set()
    delete_thread.join(5)

    assert not delete_thread.is_alive()
    assert not set_thread.is_alive()
    assert results == {"delete": {"ok": True}, "set": {"ok": True}}
    assert _rows(account_db) == [
        ("alice-b", "alice", False),
        ("alice-c", "alice", True),
        ("bob-a", "bob", True),
    ]


def test_upgrade_normalizes_legacy_defaults_and_installs_unique_index(
    tmp_path, monkeypatch
):
    """A pre-index schema upgrades without requiring newer account columns."""
    from core import database as core_db

    engine = create_engine(
        f"sqlite:///{tmp_path / 'legacy-accounts.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE email_accounts (
                    id VARCHAR PRIMARY KEY,
                    owner VARCHAR,
                    name VARCHAR NOT NULL,
                    is_default BOOLEAN NOT NULL,
                    enabled BOOLEAN NOT NULL,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))
            conn.execute(text("""
                INSERT INTO email_accounts
                    (id, owner, name, is_default, enabled, created_at, updated_at)
                VALUES
                    ('legacy-old', NULL, 'Old', 1, 1, '2024-01-01', '2024-01-01'),
                    ('legacy-new', '', 'New', 1, 1, '2025-01-01', '2025-01-01')
            """))

        monkeypatch.setattr(core_db, "engine", engine)
        core_db._migrate_email_account_default_invariant()
        core_db._migrate_email_account_default_invariant()  # idempotent replay

        with engine.connect() as conn:
            defaults = conn.execute(text("""
                SELECT id FROM email_accounts
                WHERE is_default IS TRUE
                ORDER BY id
            """)).scalars().all()
            index_names = {
                row[1] for row in conn.execute(text("PRAGMA index_list(email_accounts)"))
            }
        assert defaults == ["legacy-old"]
        assert core_db._EMAIL_ACCOUNT_DEFAULT_INDEX in index_names

        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO email_accounts
                        (id, owner, name, is_default, enabled, created_at, updated_at)
                    VALUES
                        ('legacy-third', NULL, 'Third', 1, 1, '2026-01-01', '2026-01-01')
                """))
    finally:
        engine.dispose()


def test_concurrent_legacy_seed_is_one_locked_transaction(
    tmp_path, monkeypatch, caplog
):
    from core import database as core_db

    engine = create_engine(
        f"sqlite:///{tmp_path / 'seed-accounts.db'}",
        connect_args={"check_same_thread": False, "timeout": 5},
        poolclass=NullPool,
    )
    core_db.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(
        json.dumps({"imap_host": "imap.example.test", "imap_user": "alice"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(core_db, "engine", engine)
    monkeypatch.setattr(core_db, "SessionLocal", factory)
    monkeypatch.setattr(core_db, "SETTINGS_FILE", str(settings_file))

    read_barrier = threading.Barrier(2)
    real_read_text = Path.read_text

    def synchronized_read(path, *args, **kwargs):
        value = real_read_text(path, *args, **kwargs)
        if path == settings_file:
            read_barrier.wait(5)
        return value

    monkeypatch.setattr(Path, "read_text", synchronized_read)
    threads = [
        threading.Thread(target=core_db._migrate_seed_email_account)
        for _ in range(2)
    ]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        assert all(not thread.is_alive() for thread in threads)

        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT owner, is_default FROM email_accounts
                ORDER BY id
            """)).all()
        assert rows == [(None, 1)]
        assert "seed email account migration:" not in caplog.text
    finally:
        engine.dispose()


def test_multi_owner_row_locks_are_acquired_in_canonical_order():
    from core.database import lock_email_account_owner_mutations

    class FakeSession:
        def __init__(self):
            self.locked = []

        def get_bind(self):
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def get(self, _model, owner_key, **kwargs):
            assert kwargs == {"with_for_update": True}
            self.locked.append(owner_key)
            return object()

    db = FakeSession()
    lock_email_account_owner_mutations(db, "zeta", "", "alpha", "zeta")
    assert db.locked == ["", "alpha", "zeta"]


def test_postgresql_fresh_schema_emits_default_unique_index():
    from core import database as core_db

    statements = []
    engine_holder = {}

    def capture(statement, *_args, **_kwargs):
        statements.append(
            str(statement.compile(dialect=engine_holder["engine"].dialect))
        )

    mock_engine = create_mock_engine("postgresql://", capture)
    engine_holder["engine"] = mock_engine
    core_db.EmailAccount.__table__.create(mock_engine)

    assert any(
        core_db._EMAIL_ACCOUNT_DEFAULT_INDEX in statement
        and "COALESCE(owner, '')" in statement
        and "WHERE is_default IS TRUE" in statement
        for statement in statements
    )


def test_rename_serializes_old_and_new_owner_and_stale_set_default_fails_closed(
    account_db, monkeypatch, tmp_path
):
    from core import database as core_db
    from routes import auth_routes

    _seed_account(account_db, "alice-a", "alice", is_default=True)
    _seed_account(account_db, "alice-b", "alice")
    _seed_account(account_db, "bob-a", "bob", is_default=True)

    prefs_module = types.ModuleType("routes.prefs_routes")
    prefs_module._load = lambda: {}
    prefs_module._save = lambda _data: None
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs_module)
    monkeypatch.setattr(
        auth_routes, "DEEP_RESEARCH_DIR", str(tmp_path / "deep_research")
    )
    monkeypatch.setattr(auth_routes, "MEMORY_FILE", str(tmp_path / "memory.json"))
    monkeypatch.setattr(auth_routes, "SKILLS_DIR", str(tmp_path / "skills"))

    auth_manager = mock.MagicMock()
    auth_manager.get_username_for_token.return_value = "admin"
    auth_manager.is_admin.return_value = True
    auth_manager.users = {"admin": {}, "alice": {}}
    auth_manager.rename_user.return_value = True
    rename_user = _named_endpoint(
        auth_routes.setup_auth_routes(auth_manager), "rename_user"
    )
    set_default = _endpoint("POST", "/api/email/accounts/{account_id}/set-default")

    rename_acquired = threading.Event()
    release_rename = threading.Event()
    set_attempted = threading.Event()
    set_acquired = threading.Event()
    real_lock = core_db.lock_email_account_owner_mutations

    def controlled_lock(db, *owners):
        thread_name = threading.current_thread().name
        if thread_name == "rename-owner":
            real_lock(db, *owners)
            rename_acquired.set()
            assert release_rename.wait(5)
            return
        if thread_name == "stale-set-default":
            set_attempted.set()
            real_lock(db, *owners)
            set_acquired.set()
            return
        real_lock(db, *owners)

    monkeypatch.setattr(core_db, "lock_email_account_owner_mutations", controlled_lock)
    request = SimpleNamespace(
        cookies={"odysseus_session": "admin-token"},
        app=SimpleNamespace(
            state=SimpleNamespace(
                invalidate_token_cache=lambda: None,
                session_manager=None,
                research_handler=None,
                upload_handler=None,
                personal_docs_manager=None,
            )
        ),
    )
    results = {}

    def rename_owner():
        results["rename"] = asyncio.run(
            rename_user("alice", SimpleNamespace(username="bob"), request)
        )

    def select_stale_default():
        try:
            results["set"] = asyncio.run(
                set_default("alice-b", owner="alice")
            )
        except Exception as exc:  # asserted below with its HTTP status
            results["set_error"] = exc

    rename_thread = threading.Thread(target=rename_owner, name="rename-owner")
    set_thread = threading.Thread(
        target=select_stale_default, name="stale-set-default"
    )
    rename_thread.start()
    assert rename_acquired.wait(5)
    set_thread.start()
    assert set_attempted.wait(5)
    assert not set_acquired.wait(0.1), "set-default bypassed the rename lock"

    release_rename.set()
    rename_thread.join(5)
    set_thread.join(5)

    assert not rename_thread.is_alive()
    assert not set_thread.is_alive()
    assert results["rename"]["ok"] is True
    assert isinstance(results["set_error"], HTTPException)
    assert results["set_error"].status_code == 404
    assert _rows(account_db) == [
        ("alice-a", "bob", False),
        ("alice-b", "bob", False),
        ("bob-a", "bob", True),
    ]


def test_demo_teardown_promotes_replacement_in_same_transaction(
    account_db, monkeypatch
):
    from core.database import EmailAccount
    from scripts.demo_email import demo_account

    db = account_db()
    try:
        db.add_all([
            EmailAccount(
                id="real",
                owner="",
                name="Real",
                is_default=False,
                enabled=True,
            ),
            EmailAccount(
                id="demo",
                owner="",
                name=demo_account.NAME,
                imap_user=demo_account.IMAP_USER,
                is_default=True,
                enabled=True,
            ),
        ])
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(demo_account, "SessionLocal", account_db)
    monkeypatch.setattr(demo_account, "engine", account_db.kw["bind"])

    assert demo_account.teardown() == 0
    assert _rows(account_db) == [("real", "", True)]
