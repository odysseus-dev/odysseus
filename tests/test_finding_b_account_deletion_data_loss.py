"""Regression tests for Finding B: Data Loss During Failed Account Deletion.

Invariants verified:
- B1. Token revocation failure preserves account and data in a recoverable state.
- B2. Authentication persistence failure preserves account and data.
- B3. Successful deletion purges all intended account data, notes, versions, shares, and grants.
- B4. Repeated deletion is safe, idempotent, and does not expose old data to a recreated account.
- B5. Interrupted operation between auth deletion and notes purge is safely recoverable and
      does not expose old notes to a newly created account with the same username.
- B6. Unrelated account isolation: deleting one account never deletes another account's notes or grants.
"""
import asyncio
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from contextlib import contextmanager

from core.auth import AuthManager
import core.database as cdb
from core.database import ApiToken, Note, NoteShare, NoteVersion
from core.notes_sharing import purge_notes_owned_by
from routes.auth_routes import DeleteUserRequest, SESSION_COOKIE, setup_auth_routes
from tests.helpers.database import disposable_database


@pytest.fixture
def auth_env(tmp_path, monkeypatch):
    auth_file = tmp_path / "auth.json"
    mgr = AuthManager(auth_path=str(auth_file))
    mgr.create_user("admin", "admin-pass", is_admin=True)
    mgr.create_user("alice", "alice-pass", is_admin=False)
    mgr.create_user("bob", "bob-pass", is_admin=False)

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)

        @contextmanager
        def _get_db():
            s = factory()
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
            finally:
                s.close()

        monkeypatch.setattr(cdb, "get_db_session", _get_db)

        # Helper to seed note data for a user
        def seed_user_data(username, title="Note 1"):
            with factory() as db:
                note = Note(id=f"n-{username}", owner=username, title=title, content=f"Secret of {username}")
                db.add(note)
                share = NoteShare(id=f"s-{username}", note_id=note.id, shared_with="bob" if username != "bob" else "alice", permission="view")
                db.add(share)
                version = NoteVersion(note_id=note.id, owner=username, fields='{"content": "v0"}', digest="d1", source="user", author=username)
                db.add(version)
                token = ApiToken(id=f"tok-{username}", token_hash=f"hash-{username}", token_prefix="tokpref", owner=username, name="test-token")
                db.add(token)
                db.commit()

        # Helper to invoke the delete route
        async def call_delete(username, caller="admin", invalidator=None):
            router = setup_auth_routes(mgr)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "") == "/api/auth/users" and "DELETE" in getattr(r, "methods", set()):
                    handler = r.endpoint
                    break
            assert handler is not None, "Delete route handler not found"
            req = SimpleNamespace(
                cookies={"_dummy": "x"},
                app=SimpleNamespace(state=SimpleNamespace(
                    auth_manager=mgr,
                    invalidate_token_cache=invalidator or (lambda: None),
                )),
            )
            # Mock current user session for request
            session_tok = mgr.create_session(caller, f"{caller}-pass")
            req.cookies = {SESSION_COOKIE: session_tok}
            return await handler(DeleteUserRequest(username=username), req)

        # Helper to invoke the create route
        async def call_create(username, password="password123", is_admin=False, caller="admin"):
            router = setup_auth_routes(mgr)
            handler = None
            for r in router.routes:
                if getattr(r, "path", "") == "/api/auth/users" and "POST" in getattr(r, "methods", set()):
                    handler = r.endpoint
                    break
            assert handler is not None, "Create route handler not found"
            req = SimpleNamespace(
                cookies={"_dummy": "x"},
                app=SimpleNamespace(state=SimpleNamespace(
                    auth_manager=mgr,
                )),
            )
            session_tok = mgr.create_session(caller, f"{caller}-pass")
            req.cookies = {SESSION_COOKIE: session_tok}
            from routes.auth_routes import CreateUserRequest
            return await handler(CreateUserRequest(username=username, password=password, is_admin=is_admin), req)

        yield SimpleNamespace(mgr=mgr, factory=factory, seed=seed_user_data, call_delete=call_delete, call_create=call_create)


def test_b1_token_revocation_failure(auth_env, monkeypatch):
    """B1. When API token revocation fails, account and data are preserved in a consistent, recoverable state."""
    auth_env.seed("alice")

    # Inject failure into ApiToken query deletion in delete_user
    from sqlalchemy.orm import Session
    orig_query = Session.query

    def failing_query(self, *entities, **entity_kwargs):
        if len(entities) == 1 and entities[0] is ApiToken:
            class FailingQuery:
                def filter(self, *args, **kwargs):
                    raise RuntimeError("Database error during token revocation")
            return FailingQuery()
        return orig_query(self, *entities, **entity_kwargs)

    monkeypatch.setattr(Session, "query", failing_query)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(auth_env.call_delete("alice"))

    assert exc_info.value.status_code == 400

    # User remains active in auth_manager
    assert "alice" in auth_env.mgr.users

    # User data is completely preserved
    with auth_env.factory() as db:
        assert db.get(Note, "n-alice") is not None, "Note must be preserved"
        assert db.get(NoteShare, "s-alice") is not None, "Share must be preserved"
        assert db.query(NoteVersion).filter(NoteVersion.note_id == "n-alice").first() is not None, "Version must be preserved"
        assert db.get(ApiToken, "tok-alice") is not None, "ApiToken must be preserved"


def test_b2_auth_persistence_failure(auth_env, monkeypatch):
    """B2. When persisting account removal fails, user data is not lost while account remains active."""
    auth_env.seed("alice")

    # Inject failure into auth_manager._save()
    def failing_save():
        raise OSError("Disk full / permission denied saving auth.json")

    monkeypatch.setattr(auth_env.mgr, "_save", failing_save)

    with pytest.raises(OSError):
        asyncio.run(auth_env.call_delete("alice"))

    # User remains active in auth_manager
    assert "alice" in auth_env.mgr.users

    # User data is completely preserved
    with auth_env.factory() as db:
        assert db.get(Note, "n-alice") is not None, "Note must be preserved"
        assert db.get(NoteShare, "s-alice") is not None, "Share must be preserved"
        assert db.query(NoteVersion).filter(NoteVersion.note_id == "n-alice").first() is not None, "Version must be preserved"


def test_b3_successful_deletion(auth_env):
    """B3. Successful deletion purges account, sessions, tokens, notes, versions, and shares."""
    auth_env.seed("alice")

    res = asyncio.run(auth_env.call_delete("alice"))
    assert res == {"ok": True}

    # Account removed
    assert "alice" not in auth_env.mgr.users

    # Tokens and database records removed
    with auth_env.factory() as db:
        assert db.get(Note, "n-alice") is None
        assert db.get(NoteShare, "s-alice") is None
        assert db.query(NoteVersion).filter(NoteVersion.note_id == "n-alice").first() is None
        assert db.get(ApiToken, "tok-alice") is None


def test_b4_repeated_deletion(auth_env):
    """B4. Repeated deletion is safe, idempotent, and does not expose data."""
    auth_env.seed("alice")

    # First deletion succeeds
    res1 = asyncio.run(auth_env.call_delete("alice"))
    assert res1 == {"ok": True}

    # Second deletion attempt should be handled cleanly without crashing or exposing data
    # (can_delete_user returns False, but retry purges any leftover data and succeeds or returns clean 400)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_env.call_delete("alice"))
    assert exc.value.status_code == 400


def test_b5_interrupted_operation_recovery(auth_env):
    """B5. If execution stops after auth removal but before notes purge, recreating the username never inherits old notes."""
    auth_env.seed("alice")

    # Simulate interruption: auth_manager deleted alice, but purge_notes_owned_by did not run
    assert auth_env.mgr.delete_user("alice", "admin") is True
    assert "alice" not in auth_env.mgr.users

    # Notes are still in DB due to simulated interruption
    with auth_env.factory() as db:
        assert db.get(Note, "n-alice") is not None

    # An admin retries deletion: retry cleans up orphaned notes!
    res_retry = asyncio.run(auth_env.call_delete("alice"))
    assert res_retry == {"ok": True}

    with auth_env.factory() as db:
        assert db.get(Note, "n-alice") is None

    # Alternatively, if someone signs up / recreates the account with username 'alice'
    # Seed old notes as if another interruption happened
    auth_env.seed("alice")
    res_create = asyncio.run(auth_env.call_create("alice", "new-password123"))
    assert res_create == {"ok": True}

    # The new user must not have inherited the old note!
    with auth_env.factory() as db:
        assert db.get(Note, "n-alice") is None, "Recreated account must not inherit leftover notes"


def test_b6_unrelated_account_isolation(auth_env):
    """B6. Deleting alice never touches bob's notes or bob's grants on other notes."""
    auth_env.seed("alice")
    auth_env.seed("bob")

    # seed("alice") created n-alice shared with bob (s-alice).
    # seed("bob") created n-bob shared with alice (s-bob).

    # Delete alice
    res = asyncio.run(auth_env.call_delete("alice"))
    assert res == {"ok": True}

    with auth_env.factory() as db:
        # Alice's note and its shares are gone
        assert db.get(Note, "n-alice") is None
        assert db.get(NoteShare, "s-alice") is None

        # Bob's note, version, and token are completely intact
        assert db.get(Note, "n-bob") is not None
        assert db.query(NoteVersion).filter(NoteVersion.note_id == "n-bob").first() is not None
        assert db.get(ApiToken, "tok-bob") is not None

        # Alice's grant on Bob's note was removed (alice no longer exists)
        assert db.get(NoteShare, "s-bob") is None


def test_b1_failure_before_sql_commit_rolls_back_and_restores_user(auth_env, monkeypatch):
    """Finding A: When failure occurs before SQL commit, transaction rolls back and user is restored."""
    auth_env.seed("alice")

    import core.notes_sharing as cns

    def failing_purge(*args, **kwargs):
        raise RuntimeError("Simulated failure during notes purge before SQL commit")

    monkeypatch.setattr(cns, "purge_notes_owned_by", failing_purge)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_env.call_delete("alice"))

    assert exc.value.status_code == 500
    assert "notes could not be removed" in exc.value.detail.lower()

    # User remains active in users and is restored
    assert "alice" in auth_env.mgr.users
    assert "alice" not in auth_env.mgr.deleting_users

    # Database records remain intact because SQL transaction rolled back
    with auth_env.factory() as db:
        assert db.get(Note, "n-alice") is not None


def test_b2_failure_after_sql_commit_preserves_tombstone_and_never_restores_user(auth_env, monkeypatch):
    """Finding A: When failure occurs after SQL commit, restore_user is NEVER called, tombstone is preserved,
    username is unavailable, and retry completes deletion."""
    auth_env.seed("alice")

    # Invalidate token cache tracking
    token_invalidated = False

    def on_invalidate():
        nonlocal token_invalidated
        token_invalidated = True

    # Simulate failure in finish_delete_user AFTER SQL commit
    def failing_finish(username):
        raise RuntimeError("Simulated failure in auth store saving final tombstone removal")

    monkeypatch.setattr(auth_env.mgr, "finish_delete_user", failing_finish)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_env.call_delete("alice", invalidator=on_invalidate))

    assert exc.value.status_code == 500
    assert "finalizing account deletion" in exc.value.detail.lower()

    # User must NOT be restored to users!
    assert "alice" not in auth_env.mgr.users, "restore_user must never be called after SQL commit"

    # User remains in deleting tombstone
    assert "alice" in auth_env.mgr.deleting_users
    assert auth_env.mgr.is_username_available("alice") is False

    # Notes and tokens were already permanently committed/purged in Stage 1
    with auth_env.factory() as db:
        assert db.get(Note, "n-alice") is None
        assert db.get(ApiToken, "tok-alice") is None

    # An attacker cannot register or take the username while tombstone remains
    auth_env.mgr.signup_enabled = True
    with pytest.raises(HTTPException) as exc_reg:
        asyncio.run(auth_env.call_create("alice", "attacker-password"))
    assert exc_reg.value.status_code == 409

    # Admin retries deletion: un-inject the failure so finish_delete_user succeeds
    monkeypatch.undo()

    res_retry = asyncio.run(auth_env.call_delete("alice"))
    assert res_retry == {"ok": True}

    # Now deletion is finalized, tombstone is gone, username is released
    assert "alice" not in auth_env.mgr.deleting_users
    assert auth_env.mgr.is_username_available("alice") is True
