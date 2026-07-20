"""Regressions for process-safe email-account default mutations.

The file-backed SQLite fixture uses a fresh connection for every Session.
That exercises the same database lock boundary used by separate web workers,
rather than relying on an in-process Python lock.
"""

import asyncio
import threading
from unittest import mock

import pytest
from sqlalchemy import create_engine
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
