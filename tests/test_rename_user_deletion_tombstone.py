"""A rename never takes or leaves a name held by a deletion tombstone.

An account whose deletion has started but not finished stays in the auth
store's `deleting` map, reserving its name until its data is purged.
Account creation already refused such a name; rename did not, so renaming
alice to a pending-deletion bob gave alice's notes and bob's leftover notes
the same owner, and left "bob" both active and deleting (#7376 review, P1).

Also: both names stay locked from the auth change through the ownership
migration, so no Notes write and no deletion can interleave with it.

Real AuthManager, the real auth and notes routes, a disposable database.
"""
import asyncio
import importlib
import json
import sys
import threading
import time
import types
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import core.auth as ca
import core.database as cdb
import core.notes_sharing as notes_sharing
import routes.auth_routes as ar
from core.database import Note, NoteShare
from tests.helpers.database import disposable_database

WAIT = 10.0


def _endpoint(router, method, path):
    for r in router.routes:
        if getattr(r, "path", "") == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(f"{method} {path}")


@pytest.fixture
def env(tmp_path, monkeypatch):
    # The live module, not the one imported at collection: an earlier test
    # may have re-imported core.auth or core.database, and the code under
    # test resolves them at call time.
    ca = importlib.import_module("core.auth")
    cdb = importlib.import_module("core.database")
    monkeypatch.setattr(ca, "_hash_password", lambda password: f"hash:{password}")
    monkeypatch.setattr(ca, "_verify_password", lambda password, hashed: hashed == f"hash:{password}")
    monkeypatch.setenv("AUTH_ENABLED", "true")
    # The file-backed owner stores the route also renames, kept in tmp_path.
    prefs = types.ModuleType("routes.prefs_routes")
    prefs._load = lambda: {}
    prefs._save = lambda data: None
    monkeypatch.setitem(sys.modules, "routes.prefs_routes", prefs)
    monkeypatch.setattr(ar, "DEEP_RESEARCH_DIR", str(tmp_path / "deep_research"))
    monkeypatch.setattr(ar, "MEMORY_FILE", str(tmp_path / "memory.json"))
    monkeypatch.setattr(ar, "SKILLS_DIR", str(tmp_path / "skills"))

    with disposable_database(tmp_path) as factory:
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

        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(cdb, "get_db_session", _get_db)

        mgr = ca.AuthManager(str(tmp_path / "auth.json"))
        for name in ("admin", "alice", "bob"):
            assert mgr.create_user(name, f"{name}-pass-123", is_admin=name == "admin")
        monkeypatch.setattr(ca, "_DEFAULT_AUTH_MANAGER", mgr)

        with factory() as db:
            db.add(Note(id="alice-note", owner="alice", title="a", content="alice's"))
            db.add(Note(id="bob-secret-note", owner="bob", title="b", content="bob's secret"))
            db.add(NoteShare(id="g1", note_id="bob-secret-note", shared_with="alice", permission="view"))
            db.commit()

        router = ar.setup_auth_routes(mgr)
        rename = _endpoint(router, "PUT", "/api/auth/users/{username}/rename")
        delete = _endpoint(router, "DELETE", "/api/auth/users")

        def request():
            token = mgr.create_session("admin", "admin-pass-123")
            return SimpleNamespace(
                cookies={ar.SESSION_COOKIE: token},
                app=SimpleNamespace(state=SimpleNamespace(
                    auth_manager=mgr, invalidate_token_cache=lambda: None, session_manager=None,
                    research_handler=None, upload_handler=None, personal_docs_manager=None,
                )),
                state=SimpleNamespace(current_user="admin"),
            )

        def call_rename(old, new):
            return asyncio.run(rename(old, ar.RenameUserRequest(username=new), request()))

        def call_delete(name):
            return asyncio.run(delete(ar.DeleteUserRequest(username=name), request()))

        def owners():
            with factory() as db:
                return {n.id: n.owner for n in db.query(Note).all()}

        def grants():
            with factory() as db:
                return {(g.note_id, g.shared_with, g.permission) for g in db.query(NoteShare).all()}

        yield SimpleNamespace(mgr=mgr, factory=factory, rename=call_rename, delete=call_delete,
                              owners=owners, grants=grants, auth_path=tmp_path / "auth.json")


def _interrupt_deletion(mgr, name):
    """bob's deletion stopped after the auth step, before his notes were purged."""
    assert mgr.delete_user(name, "admin")
    assert name in mgr.deleting_users and name not in mgr.users


def _status(fn, *args):
    try:
        fn(*args)
    except HTTPException as exc:
        return exc.status_code
    return 200


def test_rename_into_a_pending_deletion_name_is_refused(env):
    _interrupt_deletion(env.mgr, "bob")
    auth_before = env.auth_path.read_bytes()
    owners_before, grants_before = env.owners(), env.grants()

    assert _status(env.rename, "alice", "bob") == 409
    assert env.mgr.rename_user("alice", "bob", "admin") is False

    # Auth store, ownership and grants untouched; alice still alice.
    assert env.auth_path.read_bytes() == auth_before
    assert env.owners() == owners_before == {"alice-note": "alice", "bob-secret-note": "bob"}
    assert env.grants() == grants_before
    assert env.mgr.is_user_active("alice")
    assert "bob" not in env.mgr.users and "bob" in env.mgr.deleting_users


def test_rename_from_a_pending_deletion_state_is_refused(env):
    # An inconsistent store: alice both active and pending deletion.
    with env.mgr._cross_process_lock():
        env.mgr._config.setdefault("deleting", {})["alice"] = dict(env.mgr._config["users"]["alice"])
        env.mgr._save()
    auth_before = env.auth_path.read_bytes()
    owners_before = env.owners()

    assert _status(env.rename, "alice", "carol") == 409
    assert env.mgr.rename_user("alice", "carol", "admin") is False
    assert env.auth_path.read_bytes() == auth_before
    assert env.owners() == owners_before
    assert "carol" not in env.mgr.users


def test_a_refused_rename_leaves_the_tombstone_recoverable(env):
    _interrupt_deletion(env.mgr, "bob")
    assert _status(env.rename, "alice", "bob") == 409

    # The admin's retry finishes bob's deletion and frees the name.
    assert env.delete("bob") == {"ok": True}
    assert "bob" not in env.mgr.deleting_users and "bob" not in env.mgr.users
    # Only bob's data went; alice's note is hers and intact.
    assert env.owners() == {"alice-note": "alice"}
    with env.factory() as db:
        assert db.get(Note, "alice-note").content == "alice's"
    assert env.grants() == set()

    # Now the name is free, the rename works.
    assert env.rename("alice", "bob")["username"] == "bob"
    assert env.owners() == {"alice-note": "bob"}


def test_successful_rename_still_moves_everything(env):
    session = env.mgr.create_session("alice", "alice-pass-123")
    result = env.rename("alice", "carol")
    assert result == {"ok": True, "username": "carol", "renamed_self": False}
    assert "carol" in env.mgr.users and "alice" not in env.mgr.users
    assert env.mgr.get_username_for_token(session) == "carol"
    assert env.owners() == {"alice-note": "carol", "bob-secret-note": "bob"}
    assert env.grants() == {("bob-secret-note", "carol", "view")}
    for name in ("alice", "carol"):
        lock = env.mgr._get_user_lifecycle_lock(name)
        assert lock._writers == 0 and lock._readers == 0


def _paused_rename(env, monkeypatch):
    """Start alice -> carol and hold it inside the database ownership migration."""
    reached, resume = threading.Event(), threading.Event()
    original = notes_sharing.rename_shared_user

    def paused(db, old, new):
        reached.set()
        assert resume.wait(WAIT)
        return original(db, old, new)

    monkeypatch.setattr("core.notes_sharing.rename_shared_user", paused)
    out = {}

    def run():
        try:
            out["rename"] = env.rename("alice", "carol")
        except BaseException as exc:
            out["rename"] = exc
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert reached.wait(WAIT), "the rename never reached the ownership migration"
    return t, resume, out


def test_a_notes_write_cannot_interleave_with_the_ownership_migration(env, monkeypatch):
    from src.tools.notes import do_manage_notes

    t_rename, resume, out = _paused_rename(env, monkeypatch)
    wrote = threading.Event()

    def agent_write():
        res = asyncio.run(do_manage_notes(json.dumps({"action": "add", "title": "late", "content": "x"}),
                                          owner="alice"))
        out["write"] = res
        wrote.set()
    t_write = threading.Thread(target=agent_write, daemon=True)
    t_write.start()
    # It waits for the rename rather than writing under the old name.
    assert not wrote.wait(0.5)

    resume.set()
    t_rename.join(WAIT)
    t_write.join(WAIT)
    assert out["rename"]["username"] == "carol", out
    assert out["write"]["exit_code"] == 1, out
    # Nothing was written for the name that no longer exists.
    assert env.owners() == {"alice-note": "carol", "bob-secret-note": "bob"}


def test_a_deletion_cannot_interleave_with_the_ownership_migration(env, monkeypatch):
    t_rename, resume, out = _paused_rename(env, monkeypatch)
    deleted = threading.Event()

    def delete_new_name():
        try:
            out["delete"] = env.delete("carol")
        except BaseException as exc:
            out["delete"] = exc
        deleted.set()
    t_delete = threading.Thread(target=delete_new_name, daemon=True)
    t_delete.start()
    assert not deleted.wait(0.5), "the deletion ran while the rename held both names"

    resume.set()
    t_rename.join(WAIT)
    t_delete.join(WAIT)
    assert out["rename"]["username"] == "carol", out
    # Deleted after the rename finished: the migrated note goes with carol.
    assert out["delete"] == {"ok": True}, out
    assert env.owners() == {"bob-secret-note": "bob"}
    assert "carol" not in env.mgr.users and "alice" not in env.mgr.users


def test_a_failed_migration_rolls_back_under_the_same_lock(env, monkeypatch):
    """The compensation (auth rename back) also runs before the names are released."""
    held = []
    original = env.mgr.rename_user

    def failing(db, old, new):
        raise RuntimeError("database went away")

    def spy(old, new, requesting_user):
        lock = env.mgr._get_user_lifecycle_lock(old)
        with lock._lock:
            held.append(lock._writer_tid == threading.get_ident())
        return original(old, new, requesting_user)

    monkeypatch.setattr("core.notes_sharing.rename_shared_user", failing)
    monkeypatch.setattr(env.mgr, "rename_user", spy)
    assert _status(env.rename, "alice", "carol") == 500
    # Both the rename and its rollback ran with the lock already held.
    assert held == [True, True]
    assert "alice" in env.mgr.users and "carol" not in env.mgr.users
    assert env.owners() == {"alice-note": "alice", "bob-secret-note": "bob"}
