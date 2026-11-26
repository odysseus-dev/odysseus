"""An agent run never writes notes as a same-named account created after it started.

The request authority records the owner account's creation stamp when the
request is admitted (`RequestAuthority.owner_created`). The Notes tool hands
it to the lifecycle check, so a run that outlives its account (deleted,
then recreated under the same name while the run was paused or slow) is
refused instead of writing into the new account (#7376 review, P2). The
username alone could not tell the two accounts apart.
"""
import asyncio
import importlib
import json
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import core.auth as ca
import core.database as cdb
import routes.auth_routes as ar
import src.tools.notes as notes_tool
from core.database import Note
from src.agent_runtime.authority import RequestAuthority, bind_request_authority, create_request_authority
from tests.helpers.database import disposable_database

WAIT = 10.0


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
        assert mgr.create_user("admin", "admin-pass-123", is_admin=True)
        assert mgr.create_user("alice", "alice-pass-123")
        monkeypatch.setattr(ca, "_DEFAULT_AUTH_MANAGER", mgr)
        delete = next(r.endpoint for r in ar.setup_auth_routes(mgr).routes
                      if getattr(r, "path", "") == "/api/auth/users" and "DELETE" in getattr(r, "methods", set()))

        def delete_account(name):
            token = mgr.create_session("admin", "admin-pass-123")
            req = SimpleNamespace(
                cookies={ar.SESSION_COOKIE: token},
                app=SimpleNamespace(state=SimpleNamespace(auth_manager=mgr, invalidate_token_cache=lambda: None)),
            )
            return asyncio.run(delete(ar.DeleteUserRequest(username=name), req))

        def alice_notes():
            with factory() as db:
                return [n.title for n in db.query(Note).filter(Note.owner == "alice").all()]

        yield SimpleNamespace(mgr=mgr, factory=factory, delete=delete_account, alice_notes=alice_notes)


def test_admission_records_the_owner_account(env):
    authority = create_request_authority("add a note called milk", owner="alice")
    assert authority.owner_created == env.mgr.get_user_created_stamp("alice")
    # Kept through a persisted snapshot (continuations, approvals).
    assert RequestAuthority.from_dict(authority.to_dict()).owner_created == authority.owner_created
    # Older snapshots without the field still load.
    legacy = authority.to_dict()
    legacy.pop("owner_created")
    assert RequestAuthority.from_dict(legacy).owner_created is None


def _paused_agent_add(env, monkeypatch, title):
    """An agent run admitted for alice, paused just before it creates the note."""
    authority = create_request_authority(f"add a note called {title}", owner="alice")
    reached, resume = threading.Event(), threading.Event()
    original = notes_tool.reserve_upload_references

    def paused(*args, **kwargs):
        reached.set()
        assert resume.wait(WAIT)
        return original(*args, **kwargs)

    monkeypatch.setattr(notes_tool, "reserve_upload_references", paused)
    out = {}

    def run():
        with bind_request_authority(authority):
            out["result"] = asyncio.run(notes_tool.do_manage_notes(
                json.dumps({"action": "add", "title": title, "content": "x"}), owner="alice"))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert reached.wait(WAIT), "the agent run never reached note creation"
    return t, resume, out


def test_a_paused_run_does_not_write_into_a_recreated_account(env, monkeypatch):
    t, resume, out = _paused_agent_add(env, monkeypatch, "stale")

    # While it is paused: alice is deleted and a new alice signs up.
    assert env.delete("alice") == {"ok": True}
    assert env.mgr.create_user("alice", "new-alice-pass-123", clean_orphaned=True)

    resume.set()
    t.join(WAIT)
    assert not t.is_alive()
    assert out["result"]["exit_code"] == 1, out
    assert "recreated" in out["result"]["error"]
    assert env.alice_notes() == []


def test_a_paused_run_still_writes_when_its_account_is_unchanged(env, monkeypatch):
    t, resume, out = _paused_agent_add(env, monkeypatch, "kept")
    resume.set()
    t.join(WAIT)
    assert out["result"]["exit_code"] == 0, out
    assert env.alice_notes() == ["kept"]


@pytest.mark.parametrize("action", ["update", "toggle_item", "delete"])
def test_stale_runs_cannot_change_the_new_accounts_notes(env, action):
    authority = create_request_authority("change my note", owner="alice")
    assert env.delete("alice") == {"ok": True}
    assert env.mgr.create_user("alice", "new-alice-pass-123", clean_orphaned=True)
    with env.factory() as db:
        db.add(Note(id="new-note", owner="alice", title="new", content="- [ ] a"))
        db.commit()
        from core.note_writes import note_body_revision
        rev = note_body_revision(db.get(Note, "new-note"))
    args = {"update": {"action": "update", "id": "new-note", "content": "stale", "body_revision": rev},
            "toggle_item": {"action": "toggle_item", "id": "new-note", "index": 0, "body_revision": rev},
            "delete": {"action": "delete", "id": "new-note"}}[action]
    with bind_request_authority(authority):
        result = asyncio.run(notes_tool.do_manage_notes(json.dumps(args), owner="alice"))
    assert result["exit_code"] == 1, result
    with env.factory() as db:
        note = db.get(Note, "new-note")
        assert note is not None and note.content == "- [ ] a"
