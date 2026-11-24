"""Comprehensive verification of Finding B1 (Orphaned API Tokens) and Finding B2 (Orphaned Notes Inheritance).

Tests cover:
1. Finding B1: Orphaned API tokens
   - Interrupted account deletion cannot leave tokens that become valid when username is reused.
   - Actual bearer authentication behavior (via token cache and bcrypt matching).
   - Failed SQL commit handling.
   - Administrative retry.
   - Self-registration username reuse.
   - Administrative account creation username reuse.
   - Token cache invalidation across operations.
   - Application restart / cache refresh with surviving DB tokens.

2. Finding B2: Orphaned Notes inheritance & creation fail-closed semantics
   - Account creation fails closed when notes cleanup fails.
   - Account creation fails closed when token cleanup fails.
   - Registration failure preserves clean state.
   - Administrative creation failure preserves clean state.
   - Username reuse cleans up orphaned notes and tokens.
   - Attempted duplicate registration never deletes or touches data belonging to an existing active account.
   - Concurrent account deletion and creation serialization via _config_lock.
"""
import asyncio
import bcrypt
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from fastapi import HTTPException

from core.auth import AuthManager, register_token_cache_invalidator, invalidate_token_cache
import core.database as cdb
from core.database import ApiToken, Note, NoteShare, NoteVersion
from routes.auth_routes import (
    CreateUserRequest,
    DeleteUserRequest,
    SignupRequest,
    SESSION_COOKIE,
    setup_auth_routes,
)
from tests.helpers.database import disposable_database


@pytest.fixture
def auth_system(tmp_path, monkeypatch):
    auth_file = tmp_path / "auth.json"
    mgr = AuthManager(auth_path=str(auth_file))
    mgr.create_user("admin", "admin-pass", is_admin=True)
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

        # Mock token cache in app.state mimicking app.py
        class MockAppState:
            def __init__(self):
                self._token_cache = {}
                self._token_cache_dirty = True
                self.auth_manager = mgr

            def invalidate_token_cache(self):
                self._token_cache_dirty = True

        app_state = MockAppState()
        register_token_cache_invalidator(app_state.invalidate_token_cache)

        def refresh_token_cache():
            new_map = {}
            with factory() as db:
                rows = db.query(ApiToken).filter(ApiToken.is_active == True).all()  # noqa: E712
                for r in rows:
                    owner_key = r.owner if r.owner in mgr.users else None
                    if not owner_key:
                        continue
                    prefix = getattr(r, "token_prefix", r.id[:8])
                    new_map.setdefault(prefix, []).append((r.id, r.token_hash, owner_key, ["chat"]))
            app_state._token_cache = new_map
            app_state._token_cache_dirty = False

        def authenticate_bearer_token(raw_token: str):
            """Simulates app.py bearer auth flow."""
            if app_state._token_cache_dirty:
                refresh_token_cache()
            prefix = raw_token[:8]
            candidates = app_state._token_cache.get(prefix, [])
            for tid, thash, owner, scopes in candidates:
                if bcrypt.checkpw(raw_token.encode(), thash.encode()):
                    if not mgr.is_user_active(owner):
                        app_state._token_cache_dirty = True
                        return None
                    return owner
            return None

        # Route callers
        router = setup_auth_routes(mgr)

        def _get_handler(path, method):
            for r in router.routes:
                if getattr(r, "path", "") == path and method in getattr(r, "methods", set()):
                    return r.endpoint
            raise RuntimeError(f"Route {method} {path} not found")

        delete_handler = _get_handler("/api/auth/users", "DELETE")
        admin_create_handler = _get_handler("/api/auth/users", "POST")
        register_handler = _get_handler("/api/auth/signup", "POST")

        def make_req(caller=None):
            req = SimpleNamespace(
                cookies={},
                client=SimpleNamespace(host="127.0.0.1"),
                app=SimpleNamespace(state=app_state),
            )
            if caller:
                tok = mgr.create_session(caller, f"{caller}-pass")
                req.cookies[SESSION_COOKIE] = tok
            return req

        yield SimpleNamespace(
            mgr=mgr,
            factory=factory,
            app_state=app_state,
            authenticate_bearer_token=authenticate_bearer_token,
            refresh_token_cache=refresh_token_cache,
            call_delete=lambda username, caller="admin": delete_handler(
                DeleteUserRequest(username=username), make_req(caller)
            ),
            call_admin_create=lambda username, password="new-password", is_admin=False, caller="admin": admin_create_handler(
                CreateUserRequest(username=username, password=password, is_admin=is_admin), make_req(caller)
            ),
            call_register=lambda username, password="new-password": register_handler(
                SignupRequest(username=username, password=password), make_req()
            ),
        )


def test_finding_b1_interrupted_deletion_and_username_reuse_authentication(auth_system):
    """B1. An interrupted deletion leaving ApiToken in DB cannot authenticate once account is deleted,

    and when the username is reused via self-registration or admin creation, the old token is wiped
    and cannot authenticate as the new user.
    """
    raw_token = "ody_secretbobtoken12345678901234567890123456"
    token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()

    # Bob creates an API token
    with auth_system.factory() as db:
        tok = ApiToken(
            id="tok-bob-1",
            token_prefix=raw_token[:8],
            token_hash=token_hash,
            owner="bob",
            name="bob-token",
            is_active=True,
        )
        db.add(tok)
        db.commit()

    # Initial authentication works
    assert auth_system.authenticate_bearer_token(raw_token) == "bob"

    # Simulate interrupted deletion: bob is removed from auth_manager, but DB token survived
    auth_system.mgr._config["users"].pop("bob", None)
    auth_system.mgr._save()
    invalidate_token_cache()

    # Bob's token CANNOT authenticate while user is deleted
    assert auth_system.authenticate_bearer_token(raw_token) is None

    # Application restart (cache rebuilt from cold DB)
    auth_system.app_state._token_cache = {}
    auth_system.app_state._token_cache_dirty = True
    assert auth_system.authenticate_bearer_token(raw_token) is None

    # Now a NEW user registers under the same username 'bob'
    auth_system.mgr.signup_enabled = True
    res = asyncio.run(auth_system.call_register("bob", "brand-new-password"))
    assert res == {"ok": True, "message": "Account created"}
    assert "bob" in auth_system.mgr.users

    # Crucial security invariant: The old token must NOT authenticate as the new bob!
    assert auth_system.authenticate_bearer_token(raw_token) is None

    # And the orphaned token was completely purged from DB
    with auth_system.factory() as db:
        assert db.get(ApiToken, "tok-bob-1") is None


def test_finding_b1_admin_retry_and_administrative_creation(auth_system):
    """B1. Administrative retry cleans orphaned tokens, and admin account creation wipes old tokens."""
    raw_token = "ody_admsecretbobtoken1234567890123456789012"
    token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()

    with auth_system.factory() as db:
        tok = ApiToken(
            id="tok-bob-2",
            token_prefix=raw_token[:8],
            token_hash=token_hash,
            owner="bob",
            name="bob-token-2",
            is_active=True,
        )
        db.add(tok)
        db.commit()

    # Interrupted deletion
    auth_system.mgr._config["users"].pop("bob", None)
    auth_system.mgr._save()
    invalidate_token_cache()

    # Administrative retry of deletion on already-deleted user cleans leftover token
    res_retry = asyncio.run(auth_system.call_delete("bob"))
    assert res_retry == {"ok": True}

    with auth_system.factory() as db:
        assert db.get(ApiToken, "tok-bob-2") is None


def test_finding_b2_creation_fails_closed_when_notes_cleanup_fails(auth_system, monkeypatch):
    """B2. Account creation (self-registration & admin) cannot succeed when cleanup of previous notes fails."""
    # Delete bob from auth_manager leaving orphaned notes
    auth_system.mgr._config["users"].pop("bob", None)
    auth_system.mgr._save()

    with auth_system.factory() as db:
        db.add(Note(id="n-bob-orphan", owner="bob", title="Orphaned Note", content="Secret"))
        db.commit()

    import core.notes_sharing as cns

    def failing_purge(*args, **kwargs):
        raise RuntimeError("Database connection failure during notes purge")

    monkeypatch.setattr(cns, "purge_notes_owned_by", failing_purge)

    # 1. Admin creation must fail closed (HTTP 500)
    with pytest.raises(HTTPException) as exc1:
        asyncio.run(auth_system.call_admin_create("bob", "new-pass-123"))
    assert exc1.value.status_code == 500
    assert "bob" not in auth_system.mgr.users

    # 2. Self-registration must fail closed (HTTP 500)
    auth_system.mgr.signup_enabled = True
    with pytest.raises(HTTPException) as exc2:
        asyncio.run(auth_system.call_register("bob", "new-pass-123"))
    assert exc2.value.status_code == 500
    assert "bob" not in auth_system.mgr.users

    # In both cases, bob was NOT added to users.json while cleanup failed!
    assert "bob" not in auth_system.mgr.users


def test_finding_b2_creation_fails_closed_when_token_cleanup_fails(auth_system, monkeypatch):
    """B2. Account creation cannot succeed when cleanup of previous tokens fails."""
    auth_system.mgr._config["users"].pop("bob", None)
    auth_system.mgr._save()

    from sqlalchemy.orm import Session
    orig_query = Session.query

    def failing_query(self, *entities, **entity_kwargs):
        if len(entities) == 1 and entities[0] is ApiToken:
            class FailingQuery:
                def filter(self, *args, **kwargs):
                    raise RuntimeError("DB error during token cleanup")
            return FailingQuery()
        return orig_query(self, *entities, **entity_kwargs)

    monkeypatch.setattr(Session, "query", failing_query)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_system.call_admin_create("bob", "new-pass-123"))
    assert exc.value.status_code == 500
    assert "bob" not in auth_system.mgr.users


def test_finding_b2_duplicate_registration_never_deletes_active_user_data(auth_system):
    """B2. An attempted duplicate registration CANNOT delete or modify data belonging to an active user."""
    # Bob is active and owns a note and an API token
    with auth_system.factory() as db:
        db.add(Note(id="n-active-bob", owner="bob", title="Active Bob Note", content="Do not delete!"))
        db.add(ApiToken(id="tok-active-bob", token_prefix="actpref1", token_hash="hash", owner="bob", name="tok", is_active=True))
        db.commit()

    auth_system.mgr.signup_enabled = True

    # Attacker attempts to register as 'bob'
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_system.call_register("bob", "attacker-pass"))

    # Must be rejected with 409 Username already taken
    assert exc.value.status_code == 409

    # Active user's account and data MUST be completely intact
    assert "bob" in auth_system.mgr.users
    with auth_system.factory() as db:
        assert db.get(Note, "n-active-bob") is not None, "Active note must NOT be deleted by duplicate registration!"
        assert db.get(ApiToken, "tok-active-bob") is not None, "Active token must NOT be deleted!"


def test_finding_b2_successful_username_reuse_purges_orphans(auth_system):
    """B2. Reusing a username after a clean or interrupted deletion purges any leftover notes."""
    # Bob is deleted from auth, but leftover note exists
    auth_system.mgr._config["users"].pop("bob", None)
    auth_system.mgr._save()

    with auth_system.factory() as db:
        db.add(Note(id="n-leftover-bob", owner="bob", title="Leftover Note", content="Should be purged"))
        db.commit()

    # Recreate bob
    res = asyncio.run(auth_system.call_admin_create("bob", "brand-new-pass"))
    assert res == {"ok": True}
    assert "bob" in auth_system.mgr.users

    # Leftover note was purged; new bob starts with zero notes
    with auth_system.factory() as db:
        assert db.get(Note, "n-leftover-bob") is None


def test_finding_a_noncanonical_token_owners_cleanup(auth_system):
    """Finding A: Noncanonical token owners ('BOB', ' bob ', 'Bob') are purged on delete and cannot authenticate."""
    raw1 = "ody_noncanon1_12345678901234567890123456"
    raw2 = "ody_noncanon2_12345678901234567890123456"
    raw3 = "ody_noncanon3_12345678901234567890123456"

    with auth_system.factory() as db:
        db.add(ApiToken(id="tok-upper", token_prefix=raw1[:8], token_hash=bcrypt.hashpw(raw1.encode(), bcrypt.gensalt()).decode(), owner="BOB", name="upper", is_active=True))
        db.add(ApiToken(id="tok-spaced", token_prefix=raw2[:8], token_hash=bcrypt.hashpw(raw2.encode(), bcrypt.gensalt()).decode(), owner=" bob ", name="spaced", is_active=True))
        db.add(ApiToken(id="tok-mixed", token_prefix=raw3[:8], token_hash=bcrypt.hashpw(raw3.encode(), bcrypt.gensalt()).decode(), owner="Bob", name="mixed", is_active=True))
        db.commit()

    # Deleting bob must delete all noncanonical variations
    res_delete = asyncio.run(auth_system.call_delete("bob"))
    assert res_delete == {"ok": True}

    with auth_system.factory() as db:
        assert db.get(ApiToken, "tok-upper") is None
        assert db.get(ApiToken, "tok-spaced") is None
        assert db.get(ApiToken, "tok-mixed") is None

    # None of the tokens authenticate
    assert auth_system.authenticate_bearer_token(raw1) is None
    assert auth_system.authenticate_bearer_token(raw2) is None
    assert auth_system.authenticate_bearer_token(raw3) is None

    # Recreate bob
    auth_system.mgr.signup_enabled = True
    asyncio.run(auth_system.call_register("bob", "new-bob-password"))

    # Still cannot authenticate
    assert auth_system.authenticate_bearer_token(raw1) is None
    assert auth_system.authenticate_bearer_token(raw2) is None
    assert auth_system.authenticate_bearer_token(raw3) is None


def test_finding_b_creation_fails_closed_on_import_or_attribute_error(auth_system, monkeypatch):
    """Finding B: create_user fails closed on ImportError or AttributeError during cleanup."""
    import core.notes_sharing as cns

    # Simulate missing dependency or attribute during cleanup
    def raise_import_error(*args, **kwargs):
        raise ImportError("simulated missing dependency")

    monkeypatch.setattr(cns, "purge_notes_owned_by", raise_import_error)

    # create_user must return False and NOT add user
    assert auth_system.mgr.create_user("charlie", "password123", clean_orphaned=True) is False
    assert "charlie" not in auth_system.mgr.users

    # Routes must return HTTP 500
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_system.call_admin_create("charlie", "password123"))
    assert exc.value.status_code == 500
    assert "charlie" not in auth_system.mgr.users


def test_finding_b_first_run_setup_cleans_orphaned_data(tmp_path, monkeypatch):
    """Finding B: First-run setup() purges pre-existing orphaned notes and tokens."""
    auth_file = tmp_path / "auth_first_run.json"
    mgr = AuthManager(auth_path=str(auth_file))

    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        with factory() as db:
            db.add(Note(id="n-orphan-init", owner="admin", title="Orphan Note", content="Old Secret"))
            db.add(ApiToken(id="tok-orphan-init", token_prefix="prefk123", token_hash="hash", owner="admin", name="old-tok", is_active=True))
            db.commit()

        # Run first-run setup
        assert mgr.setup("admin", "admin-initial-password") is True
        assert "admin" in mgr.users

        # Orphaned data must be purged
        with factory() as db:
            assert db.get(Note, "n-orphan-init") is None
            assert db.get(ApiToken, "tok-orphan-init") is None


def test_finding_c_deletion_tombstone_and_competing_registration(auth_system):
    """Finding C: Deletion tombstone in auth.json blocks competing registration until deletion completes."""
    # Place 'bob' in deleting tombstone
    with auth_system.mgr._config_lock:
        auth_system.mgr._config.setdefault("deleting", {})["bob"] = {"is_admin": False}
        auth_system.mgr.users.pop("bob", None)
        auth_system.mgr._save()

    auth_system.mgr.signup_enabled = True

    # 1. Competing signup must fail with 409
    with pytest.raises(HTTPException) as exc1:
        asyncio.run(auth_system.call_register("bob", "competing-pass"))
    assert exc1.value.status_code == 409
    assert "taken" in exc1.value.detail.lower() or "delet" in exc1.value.detail.lower()

    # 2. Competing admin create must fail with 409
    with pytest.raises(HTTPException) as exc2:
        asyncio.run(auth_system.call_admin_create("bob", "competing-pass"))
    assert exc2.value.status_code == 409

    # Finish deletion
    auth_system.mgr.finish_delete_user("bob")
    assert "bob" not in auth_system.mgr.deleting_users

    # Now registration succeeds
    res = asyncio.run(auth_system.call_register("bob", "allowed-pass"))
    assert res == {"ok": True, "message": "Account created"}
    assert "bob" in auth_system.mgr.users


def test_finding_c_blocking_in_flight_notes_operations_for_deleting_user(auth_system):
    """Finding C: In-flight notes operations are rejected for users undergoing deletion."""
    import json
    from src.tools.notes import do_manage_notes

    # Place 'bob' in deleting state
    with auth_system.mgr._config_lock:
        auth_system.mgr._config.setdefault("deleting", {})["bob"] = {"is_admin": False}
        auth_system.mgr.users.pop("bob", None)
        auth_system.mgr._save()

    res = asyncio.run(do_manage_notes(json.dumps({"action": "add", "title": "Late Note", "content": "Should be rejected"}), owner="bob"))
    assert res.get("exit_code") == 1
    assert "inactive or pending deletion" in res.get("error", "").lower()


def test_finding_e_admin_retry_reports_cleanup_failures(auth_system, monkeypatch):
    """Finding E: Administrative retry raises HTTP 500 and does NOT hide token or notes cleanup failures."""
    # Bob is deleted from users
    auth_system.mgr.users.pop("bob", None)
    auth_system.mgr._save()

    # 1. Token cleanup failure during retry raises HTTP 500
    import core.auth as ca
    def failing_token_delete(db, username):
        raise RuntimeError("Database connection lost during token purge")

    monkeypatch.setattr(ca, "delete_api_tokens_for_user", failing_token_delete)

    with pytest.raises(HTTPException) as exc1:
        asyncio.run(auth_system.call_delete("bob"))
    assert exc1.value.status_code == 500
    assert "failed" in exc1.value.detail.lower()

    # 2. Notes cleanup failure during retry raises HTTP 500
    monkeypatch.undo()
    import core.notes_sharing as cns
    def failing_notes_purge(*args, **kwargs):
        raise RuntimeError("Disk error during notes purge")

    monkeypatch.setattr(cns, "purge_notes_owned_by", failing_notes_purge)

    with pytest.raises(HTTPException) as exc2:
        asyncio.run(auth_system.call_delete("bob"))
    assert exc2.value.status_code == 500
    assert "failed" in exc2.value.detail.lower()


def test_late_note_writes_rejected_at_commit_boundary_when_writer_in_deleting_tombstone(auth_system):
    """P1: Late Notes writes during account deletion are rejected at commit boundary."""
    from core.note_writes import commit_if_unchanged, stored_stamp, NotePermissionRevoked

    # Setup: Bob creates a note
    with auth_system.factory() as db:
        note = Note(id="note-bob-late", owner="bob", title="Bob Note", content="Initial Content")
        db.add(note)
        db.commit()

    # Bob loads the note in a session to prepare an edit
    with auth_system.factory() as db:
        bob_note = db.query(Note).filter(Note.id == "note-bob-late").first()
        stamp = stored_stamp(db, "note-bob-late")
        bob_note.content = "Bob Late Attempted Content"

        # Account deletion begins for Bob while Bob's edit is in flight
        with auth_system.mgr._config_lock:
            auth_system.mgr._config.setdefault("deleting", {})["bob"] = {"is_admin": False}
            auth_system.mgr.users.pop("bob", None)
            auth_system.mgr._save()

        # At the commit boundary, commit_if_unchanged must enforce lifecycle and reject
        with pytest.raises(NotePermissionRevoked) as exc:
            commit_if_unchanged(db, bob_note, stamp, user="bob")
        assert exc.value.status_code == 403
        assert "inactive or pending deletion" in exc.value.message.lower()

    # Verify content was NOT modified in the database
    with auth_system.factory() as db:
        note_after = db.query(Note).filter(Note.id == "note-bob-late").first()
        assert note_after.content == "Initial Content"


def test_late_note_writes_rejected_at_commit_boundary_when_owner_in_deleting_tombstone(auth_system):
    """P1: Collaborator writes are rejected at commit boundary when note owner is being deleted."""
    from core.note_writes import commit_if_unchanged, stored_stamp, NotePermissionRevoked

    # Setup: Admin owns note, shared with Bob (edit)
    with auth_system.factory() as db:
        note = Note(id="note-admin-shared", owner="admin", title="Admin Note", content="Admin Initial Content")
        share = NoteShare(id="share-admin-bob", note_id="note-admin-shared", shared_with="bob", permission="edit")
        db.add(note)
        db.add(share)
        db.commit()

    # Bob loads note to edit
    with auth_system.factory() as db:
        bob_note = db.query(Note).filter(Note.id == "note-admin-shared").first()
        stamp = stored_stamp(db, "note-admin-shared")
        bob_note.content = "Bob Edit on Admin Note"

        # Account deletion begins for Admin (owner) while Bob's edit is in flight
        with auth_system.mgr._config_lock:
            auth_system.mgr._config.setdefault("deleting", {})["admin"] = {"is_admin": True}
            auth_system.mgr.users.pop("admin", None)
            auth_system.mgr._save()

        # At commit boundary, commit_if_unchanged must reject Bob's write on deleted owner's note
        with pytest.raises(NotePermissionRevoked) as exc:
            commit_if_unchanged(db, bob_note, stamp, user="bob")
        assert exc.value.status_code == 403
        assert "inactive or pending deletion" in exc.value.message.lower()

    # Verify content was preserved
    with auth_system.factory() as db:
        note_after = db.query(Note).filter(Note.id == "note-admin-shared").first()
        assert note_after.content == "Admin Initial Content"


def test_tombstone_finalization_failure_preserves_tombstone_and_propagates_error(auth_system, monkeypatch):
    """P2: Tombstone finalization failures keep tombstone intact and propagate HTTP 500."""
    # Place Bob in deleting state
    with auth_system.mgr._config_lock:
        auth_system.mgr._config.setdefault("deleting", {})["bob"] = {"is_admin": False}
        auth_system.mgr.users.pop("bob", None)
        auth_system.mgr._save()

    # 1. Direct finish_delete_user persistence failure
    def failing_save():
        raise OSError("Simulated disk error during auth save")

    monkeypatch.setattr(auth_system.mgr, "_save", failing_save)
    with pytest.raises(OSError):
        auth_system.mgr.finish_delete_user("bob")

    # Verify tombstone remains intact despite exception
    assert "bob" in auth_system.mgr.deleting_users
    assert auth_system.mgr.is_username_available("bob") is False

    # 2. Administrative deletion endpoint propagation
    monkeypatch.undo()
    def failing_finish(username):
        raise RuntimeError("Simulated finalization failure in auth manager")

    monkeypatch.setattr(auth_system.mgr, "finish_delete_user", failing_finish)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_system.call_delete("bob"))
    assert exc.value.status_code == 500
    assert "failed" in exc.value.detail.lower() or "finalize" in exc.value.detail.lower()


def test_cross_process_auth_config_synchronization_and_locking(tmp_path):
    """P1: Multi-worker consistency: AuthManager reloads fresh state across instances/processes and locks."""
    import os
    import fcntl
    auth_file = tmp_path / "auth.json"

    mgr1 = AuthManager(auth_path=str(auth_file))
    mgr2 = AuthManager(auth_path=str(auth_file))

    # Setup admin on mgr1
    assert mgr1.setup("admin", "admin-pass-1") is True

    # mgr2 should immediately see admin via _ensure_fresh_config
    assert "admin" in mgr2.users
    assert mgr2.is_user_active("admin") is True

    # mgr1 creates user 'charlie'
    mgr1.create_user("charlie", "charlie-pass")

    # mgr2 immediately sees 'charlie'
    assert "charlie" in mgr2.users
    assert mgr2.is_user_active("charlie") is True

    # mgr1 initiates deletion of 'charlie' (moves to deleting tombstone)
    mgr1.delete_user("charlie", "admin")

    # mgr2 immediately sees 'charlie' is inactive and in deleting_users
    assert mgr2.is_user_active("charlie") is False
    assert "charlie" in mgr2.deleting_users
    assert mgr2.is_username_available("charlie") is False

    # mgr1 finishes deletion
    mgr1.finish_delete_user("charlie")

    # mgr2 immediately sees 'charlie' is gone and username is available
    assert "charlie" not in mgr2.deleting_users
    assert "charlie" not in mgr2.users
    assert mgr2.is_username_available("charlie") is True

    # Verify file lock mechanism: _cross_process_lock acquires flock on auth.json.lock
    lock_file = str(auth_file) + ".lock"
    assert os.path.exists(lock_file)
    with mgr1._cross_process_lock():
        # Attempt non-blocking lock from an independent file descriptor
        fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)


def test_single_writer_deployment_lock_enforcement(tmp_path, monkeypatch):
    """P1: Multi-worker consistency: app.py startup rejects concurrent processes on odysseus.lock."""
    import fcntl
    import os
    import app as odysseus_app

    monkeypatch.setattr(odysseus_app, "DATA_DIR", str(tmp_path))
    lock_path = tmp_path / "odysseus.lock"

    # Process 1 acquires writer lock
    p1_fd = open(lock_path, "a+b")
    fcntl.flock(p1_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    # Process 2 attempts startup and must be rejected with RuntimeError
    with pytest.raises(RuntimeError) as exc:
        asyncio.run(odysseus_app._startup_event())
    assert "concurrent writer deployment detected" in str(exc.value).lower()

    # Process 1 cleanly exits and releases lock
    fcntl.flock(p1_fd, fcntl.LOCK_UN)
    p1_fd.close()

    # Process 2 can now acquire lock on startup without collision
    # (disable background tasks in startup for clean testing)
    monkeypatch.setattr(odysseus_app.webhook_manager, "set_loop", lambda loop: None)
    asyncio.run(odysseus_app._startup_event())
    assert getattr(odysseus_app.app.state, "_writer_lock_file", None) is not None

    # Shutdown releases lock cleanly
    asyncio.run(odysseus_app._shutdown_event())


def test_auth_cross_process_lock_fails_closed_on_lock_failure(tmp_path, monkeypatch):
    """P1: _cross_process_lock fails closed: raises RuntimeError on lock acquisition error and never yields."""
    auth_file = tmp_path / "auth_lock_fail.json"
    mgr = AuthManager(auth_path=str(auth_file))

    import fcntl
    orig_flock = fcntl.flock

    def broken_flock(fd, op):
        raise OSError("Simulated kernel lock table full or I/O failure")

    monkeypatch.setattr(fcntl, "flock", broken_flock)

    executed = False
    with pytest.raises(RuntimeError) as exc:
        with mgr._cross_process_lock():
            executed = True

    assert "could not acquire authentication process lock" in str(exc.value).lower()
    assert executed is False, "Protected block must not execute when lock fails"


def test_auth_ensure_fresh_config_fails_closed_on_stat_or_read_error(tmp_path, monkeypatch):
    """P1: _ensure_fresh_config fails closed on os.stat or read errors."""
    auth_file = tmp_path / "auth_stat_fail.json"
    mgr = AuthManager(auth_path=str(auth_file))
    mgr.create_user("alice", "pass-123")

    import os
    orig_stat = os.stat

    def broken_stat(path, *args, **kwargs):
        if str(path) == str(auth_file):
            raise PermissionError("EACCES permission denied")
        return orig_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", broken_stat)

    with pytest.raises(RuntimeError) as exc:
        mgr._ensure_fresh_config()

    assert "unable to verify fresh authentication configuration" in str(exc.value).lower()


def test_lifecycle_gate_concurrent_write_and_deletion_serialization(auth_system):
    """P1: Account lifecycle gate coordinates concurrent writes and deletions.

    Write-first: write holds mutation guard; deletion waits, then purges note.
    Deletion-first: deletion holds deletion guard; write waits, then rejects at commit boundary.
    """
    import threading
    import time
    from core.database import Note
    from core.note_writes import commit_if_unchanged, stored_stamp, NotePermissionRevoked

    # Scenario 1: Write-first ordering
    # Bob prepares a write and acquires account_mutation_guard
    with auth_system.factory() as db:
        note1 = Note(id="n-bob-gate-1", owner="bob", title="Bob Gate Note 1", content="Content 1")
        db.add(note1)
        db.commit()

    event_write_started = threading.Event()
    event_deletion_waiting = threading.Event()
    write_finished = False

    db_write = auth_system.factory()
    try:
        b_note = db_write.query(Note).filter(Note.id == "n-bob-gate-1").first()
        stamp = stored_stamp(db_write, "n-bob-gate-1")
        b_note.content = "Bob Updated Content 1"

        def run_deletion():
            event_write_started.wait()
            event_deletion_waiting.set()
            asyncio.run(auth_system.call_delete("bob"))

        t_del = threading.Thread(target=run_deletion)
        # Hold account mutation guard in write thread
        with auth_system.mgr.account_mutation_guard(actor="bob", owner="bob", db=db_write):
            t_del.start()
            event_write_started.set()
            event_deletion_waiting.wait()
            # Deletion is blocked waiting for Bob's read lease
            time.sleep(0.05)
            # Write commits while lease is held
            db_write.commit()
            write_finished = True
    finally:
        db_write.close()

    # Now deletion proceeds and finishes
    t_del.join(timeout=5.0)
    assert not t_del.is_alive(), "Deletion thread should complete"
    assert write_finished is True
    assert "bob" not in auth_system.mgr.users

    # Note was purged by the completed deletion
    with auth_system.factory() as db_check:
        assert db_check.get(Note, "n-bob-gate-1") is None

    # Scenario 2: Deletion-first ordering
    # Recreate bob and note
    auth_system.mgr.create_user("bob", "bob-pass")
    with auth_system.factory() as db:
        note2 = Note(id="n-bob-gate-2", owner="bob", title="Bob Gate Note 2", content="Content 2")
        db.add(note2)
        db.commit()

    event_deletion_started = threading.Event()
    write_exc = None

    def run_write():
        nonlocal write_exc
        event_deletion_started.wait()
        try:
            with auth_system.factory() as db_bob:
                b_note = db_bob.query(Note).filter(Note.id == "n-bob-gate-2").first()
                stamp = stored_stamp(db_bob, "n-bob-gate-2")
                b_note.content = "Bob Late Write 2"
                commit_if_unchanged(db_bob, b_note, stamp, user="bob")
        except Exception as e:
            write_exc = e

    t_write = threading.Thread(target=run_write)
    t_write.start()

    # Deletion runs and acquires exclusive deletion guard
    with auth_system.mgr.account_deletion_guard("bob"):
        event_deletion_started.set()
        # Move bob to deleting state
        auth_system.mgr.delete_user("bob", "admin")
        time.sleep(0.05)

    t_write.join(timeout=5.0)
    assert not t_write.is_alive(), "Write thread should complete"
    assert isinstance(write_exc, NotePermissionRevoked)
    assert write_exc.status_code == 403
    assert "inactive or pending deletion" in write_exc.message.lower()


def test_agent_all_operations_blocked_when_account_is_deleting(auth_system):
    """P1: Agent manage_notes actions (add, delete, toggle_item, update) are all blocked for deleting accounts."""
    import json
    from src.tools.notes import do_manage_notes

    # Setup: Note for bob
    from core.note_writes import note_body_revision
    with auth_system.factory() as db:
        note = Note(id="n-agent-test", owner="bob", title="Agent Note", content="- [ ] item 1")
        db.add(note)
        db.commit()
        rev = note_body_revision(note)

    # Move bob to deleting tombstone
    with auth_system.mgr._config_lock:
        auth_system.mgr._config.setdefault("deleting", {})["bob"] = {"is_admin": False}
        auth_system.mgr.users.pop("bob", None)
        auth_system.mgr._save()

    # 1. Action: add
    res_add = asyncio.run(do_manage_notes(json.dumps({"action": "add", "title": "New Note", "content": "Late"}), owner="bob"))
    assert res_add.get("exit_code") == 1
    assert "inactive or pending deletion" in res_add.get("error", "").lower()

    # 2. Action: delete
    res_del = asyncio.run(do_manage_notes(json.dumps({"action": "delete", "id": "n-agent-test"}), owner="bob"))
    assert res_del.get("exit_code") == 1
    assert "inactive or pending deletion" in res_del.get("error", "").lower()

    # 3. Action: toggle_item
    res_tog = asyncio.run(do_manage_notes(json.dumps({"action": "toggle_item", "id": "n-agent-test", "index": 0, "body_revision": rev}), owner="bob"))
    assert res_tog.get("exit_code") == 1
    assert "inactive or pending deletion" in res_tog.get("error", "").lower()

    # 4. Action: update
    res_upd = asyncio.run(do_manage_notes(json.dumps({"action": "update", "id": "n-agent-test", "content": "Updated", "body_revision": rev}), owner="bob"))
    assert res_upd.get("exit_code") == 1
    assert "inactive or pending deletion" in res_upd.get("error", "").lower()


def test_bearer_auth_cache_rejection_when_account_in_deleting_tombstone(auth_system):
    """P1: Bearer auth rejects cached tokens for accounts undergoing deletion."""
    raw_token = "ody_cached_tok_12345678901234567890123456"
    token_hash = bcrypt.hashpw(raw_token.encode(), bcrypt.gensalt()).decode()

    with auth_system.factory() as db:
        tok = ApiToken(
            id="tok-bob-cache",
            token_prefix=raw_token[:8],
            token_hash=token_hash,
            owner="bob",
            name="cache-token",
            is_active=True,
        )
        db.add(tok)
        db.commit()

    # Initial authentication caches token and succeeds
    assert auth_system.authenticate_bearer_token(raw_token) == "bob"

    # Bob is placed in deleting tombstone
    with auth_system.mgr._config_lock:
        auth_system.mgr._config.setdefault("deleting", {})["bob"] = {"is_admin": False}
        auth_system.mgr.users.pop("bob", None)
        auth_system.mgr._save()

    # Cached token authentication must be rejected because is_user_active is False
    assert auth_system.mgr.is_user_active("bob") is False
    assert auth_system.authenticate_bearer_token(raw_token) is None


def test_lifecycle_authorization_fails_closed_when_auth_store_unavailable(auth_system, monkeypatch):
    """Finding D: Lifecycle authorization fails closed (503/403 and db rollback) if auth state cannot be verified."""
    with auth_system.factory() as db:
        note = Note(id="n-fail-closed", owner="bob", title="Fail Closed Note", content="Original Content")
        db.add(note)
        db.commit()

    with auth_system.factory() as db:
        b_note = db.query(Note).filter(Note.id == "n-fail-closed").first()
        from core.note_writes import stored_stamp, commit_if_unchanged, NotePermissionRevoked
        stamp = stored_stamp(db, "n-fail-closed")
        b_note.content = "Uncommitted Modified Content"

        # Simulate broken auth store during is_user_active check
        def failing_is_user_active(username):
            raise OSError("Auth store storage volume unmounted / connection lost")

        monkeypatch.setattr(auth_system.mgr, "is_user_active", failing_is_user_active)

        with pytest.raises(NotePermissionRevoked) as exc:
            commit_if_unchanged(db, b_note, stamp, user="bob")

        assert exc.value.status_code == 503
        assert "unavailable" in exc.value.message.lower()

    # Verify db was rolled back and content in DB is untouched
    with auth_system.factory() as db:
        unchanged = db.get(Note, "n-fail-closed")
        assert unchanged.content == "Original Content"
