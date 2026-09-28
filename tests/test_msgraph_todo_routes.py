"""Route-level coverage for the Microsoft To Do connection.

Proves the HTTP surface behaves: every local change to a task note queues a
write-back, ticking one off completes it upstream, deleting one leaves the
tombstone the push needs, and the OAuth callback refuses anything it cannot
verify.

Transport note: these drive the ASGI app through ``httpx.ASGITransport``
rather than ``starlette.testclient.TestClient``, matching
``test_notes_fail_closed_auth.py`` — and because the write-back runs as a
FastAPI background task, which only executes when the real request cycle
does.
"""

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
import routes.note_routes as nr
from core.database import MsTodoDeletedNote, Note
from src import msgraph_todo as mt

_PEER = ("203.0.113.7", 54321)
USER = "alice"


class _Identity:
    """Pure-ASGI shim mirroring what the auth middleware writes onto
    request.state — it stays off Starlette's BaseHTTPMiddleware path."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            state = scope.setdefault("state", {})
            user = headers.get(b"x-test-user")
            if user:
                state["current_user"] = user.decode()
        await self.app(scope, receive, send)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A configured-auth world with an empty notes DB and the Graph push
    intercepted. Returns (client_factory, session_factory, pushes)."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'notes.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(nr, "SessionLocal", factory)
    monkeypatch.setattr(cdb, "SessionLocal", factory)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)

    pushes = []
    monkeypatch.setattr(mt, "push_note_blocking",
                        lambda owner, note_id, action: pushes.append((owner, note_id, action)))

    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(nr.setup_note_routes())

    def _client():
        transport = httpx.ASGITransport(app=_Identity(app), client=_PEER)
        return httpx.AsyncClient(
            transport=transport, base_url="http://notes.test",
            headers={"x-test-user": USER},
        )

    return _client, factory, pushes


def _note(factory, **over):
    fields = {
        "id": str(uuid.uuid4()), "owner": USER, "title": "task",
        "note_type": "todo",
    }
    fields.update(over)
    db = factory()
    try:
        db.add(Note(**fields))
        db.commit()
    finally:
        db.close()
    return fields["id"]


def _get(factory, note_id):
    db = factory()
    try:
        return db.query(Note).filter(Note.id == note_id).first()
    finally:
        db.close()


# ── Write-back is queued on every local change ────────────────────

@pytest.mark.asyncio
async def test_creating_a_todo_queues_a_push_and_marks_it_pending(env):
    client_factory, factory, pushes = env
    async with client_factory() as client:
        res = await client.post("/api/notes", json={"title": "Buy milk", "note_type": "todo"})
    assert res.status_code == 200
    note_id = res.json()["id"]
    assert pushes == [(USER, note_id, "create")]
    # The marker is written in the same transaction as the note, so a crash
    # between the commit and the push still leaves the retry behind.
    assert _get(factory, note_id).todo_sync_pending == "create"


@pytest.mark.asyncio
async def test_creating_a_plain_note_queues_nothing(env):
    """Writing every scratch note into someone's task list would be a
    surprise, and a plain note has no shape in To Do."""
    client_factory, factory, pushes = env
    async with client_factory() as client:
        res = await client.post("/api/notes", json={"title": "idea", "note_type": "note"})
    assert res.status_code == 200
    assert pushes == []
    assert _get(factory, res.json()["id"]).todo_sync_pending is None


@pytest.mark.asyncio
async def test_editing_a_task_queues_an_update(env):
    client_factory, factory, pushes = env
    note_id = _note(factory, remote_id="T1", remote_list_id="L1")
    async with client_factory() as client:
        res = await client.put(f"/api/notes/{note_id}", json={"title": "Buy oat milk"})
    assert res.status_code == 200
    assert pushes == [(USER, note_id, "update")]


@pytest.mark.asyncio
async def test_archiving_a_task_completes_it_upstream(env):
    """Archiving is what "done" means for a note: it leaves the grid here and
    the task is ticked off in To Do."""
    client_factory, factory, pushes = env
    note_id = _note(factory, remote_id="T1", remote_list_id="L1")
    async with client_factory() as client:
        res = await client.post(f"/api/notes/{note_id}/archive")
    assert res.json()["archived"] is True
    assert pushes == [(USER, note_id, "update")]


@pytest.mark.asyncio
async def test_ticking_a_checklist_item_queues_an_update(env):
    client_factory, factory, pushes = env
    note_id = _note(factory, remote_id="T1", remote_list_id="L1",
                    items=json.dumps([{"text": "2%", "done": False}]))
    async with client_factory() as client:
        res = await client.post(f"/api/notes/{note_id}/items/0/toggle")
    assert res.json()["items"][0]["done"] is True
    assert pushes == [(USER, note_id, "update")]


@pytest.mark.asyncio
async def test_pinning_a_task_queues_an_update(env):
    client_factory, factory, pushes = env
    note_id = _note(factory, remote_id="T1", remote_list_id="L1")
    async with client_factory() as client:
        await client.post(f"/api/notes/{note_id}/pin")
    assert pushes == [(USER, note_id, "update")]


@pytest.mark.asyncio
async def test_pinning_a_plain_note_queues_nothing(env):
    client_factory, factory, pushes = env
    note_id = _note(factory, note_type="note")
    async with client_factory() as client:
        await client.post(f"/api/notes/{note_id}/pin")
    assert pushes == []


# ── Deletes ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deleting_a_task_leaves_a_tombstone_and_queues_the_delete(env):
    """The note row is gone by the time the push runs, so the remote ids
    have nowhere else to live."""
    client_factory, factory, pushes = env
    note_id = _note(factory, remote_id="T1", remote_list_id="L1", todo_account_id="a1")
    async with client_factory() as client:
        res = await client.delete(f"/api/notes/{note_id}")
    assert res.status_code == 200
    assert pushes == [(USER, note_id, "delete")]
    db = factory()
    try:
        tomb = db.query(MsTodoDeletedNote).filter(MsTodoDeletedNote.id == note_id).first()
        assert tomb is not None
        assert (tomb.remote_id, tomb.remote_list_id, tomb.account_id) == ("T1", "L1", "a1")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_deleting_a_task_that_never_reached_graph_queues_nothing(env):
    client_factory, factory, pushes = env
    note_id = _note(factory)
    async with client_factory() as client:
        await client.delete(f"/api/notes/{note_id}")
    assert pushes == []
    assert factory().query(MsTodoDeletedNote).count() == 0


@pytest.mark.asyncio
async def test_turning_a_task_back_into_a_note_removes_it_upstream(env):
    """It is no longer a task, so it should not stay in the task list."""
    client_factory, factory, pushes = env
    note_id = _note(factory, remote_id="T1", remote_list_id="L1")
    async with client_factory() as client:
        res = await client.put(f"/api/notes/{note_id}", json={"note_type": "note"})
    assert res.status_code == 200
    assert pushes == [(USER, note_id, "delete")]
    note = _get(factory, note_id)
    assert note.remote_id is None and note.origin is None
    assert factory().query(MsTodoDeletedNote).count() == 1


# ── Connection management ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_connected_accounts_never_return_tokens(env, monkeypatch):
    client_factory, _, _ = env
    monkeypatch.setattr(mt, "_load_mstodo_accounts", lambda owner: [{
        "id": "a1", "label": "work", "email": "me@corp.com",
        "access_token": "SECRET", "refresh_token": "ALSO-SECRET",
    }])
    async with client_factory() as client:
        res = await client.get("/api/notes/config/microsoft")
    body = res.text
    assert res.status_code == 200
    assert "SECRET" not in body and "ALSO-SECRET" not in body
    assert res.json()["accounts"][0]["connected"] is True


@pytest.mark.asyncio
async def test_disconnecting_unlinks_the_notes_but_keeps_them(env, monkeypatch):
    """They are the user's tasks: deleting them to tidy up a setting would
    destroy local data."""
    client_factory, factory, _ = env
    note_id = _note(factory, remote_id="T1", remote_list_id="L1",
                    todo_account_id="a1", origin="mstodo")
    saved = {}
    monkeypatch.setattr(mt, "_load_mstodo_accounts", lambda owner: [{"id": "a1"}])
    monkeypatch.setattr(mt, "_save_mstodo_accounts",
                        lambda owner, accounts: saved.update(accounts=accounts))
    async with client_factory() as client:
        res = await client.delete("/api/notes/config/microsoft/a1")
    assert res.status_code == 200
    assert saved["accounts"] == []
    note = _get(factory, note_id)
    assert note is not None
    assert note.remote_id is None and note.todo_account_id is None


@pytest.mark.asyncio
async def test_disconnecting_an_unknown_account_is_a_404(env, monkeypatch):
    client_factory, _, _ = env
    monkeypatch.setattr(mt, "_load_mstodo_accounts", lambda owner: [{"id": "a1"}])
    async with client_factory() as client:
        res = await client.delete("/api/notes/config/microsoft/nope")
    assert res.status_code == 404


# ── Sync ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sync_defaults_to_both_directions(env, monkeypatch):
    """A pull alone would strand a task created here while offline, and
    "Sync" is exactly when the user expects that retry to happen."""
    client_factory, _, _ = env
    seen = {}

    async def _fake(owner, direction):
        seen["direction"] = direction
        return {"tasks": 0, "errors": []}

    monkeypatch.setattr(mt, "_load_mstodo_accounts", lambda owner: [{"id": "a1"}])
    monkeypatch.setattr(mt, "sync_mstodo_direction", _fake)
    async with client_factory() as client:
        res = await client.post("/api/notes/sync")
    assert res.status_code == 200
    assert seen["direction"] == "both"


@pytest.mark.asyncio
async def test_sync_with_nothing_connected_says_so(env, monkeypatch):
    client_factory, _, _ = env
    monkeypatch.setattr(mt, "_load_mstodo_accounts", lambda owner: [])
    async with client_factory() as client:
        res = await client.post("/api/notes/sync")
    assert res.status_code == 200
    assert "No Microsoft To Do account" in res.json()["errors"][0]


# ── OAuth ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_authorize_without_an_app_registration_explains_itself(env, monkeypatch):
    """Bouncing the user to a Microsoft error page would tell them nothing."""
    client_factory, _, _ = env
    monkeypatch.delenv("MICROSOFT_OAUTH_CLIENT_ID", raising=False)
    async with client_factory() as client:
        res = await client.get("/api/notes/oauth/microsoft/authorize")
    assert res.status_code == 400
    assert "MICROSOFT_OAUTH_CLIENT_ID" in res.json()["detail"]


@pytest.mark.asyncio
async def test_authorize_requests_only_the_tasks_scope(env, monkeypatch):
    client_factory, _, _ = env
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "cid")
    async with client_factory() as client:
        res = await client.get("/api/notes/oauth/microsoft/authorize",
                               follow_redirects=False)
    assert res.status_code in (302, 307)
    target = res.headers["location"]
    assert "Tasks.ReadWrite" in target
    assert "Calendars" not in target and "IMAP" not in target


@pytest.mark.asyncio
async def test_a_forged_state_is_refused(env, monkeypatch):
    """The state is HMAC-signed; anything else is someone else's redirect."""
    client_factory, _, _ = env
    saved = []
    monkeypatch.setattr(mt, "_save_mstodo_accounts",
                        lambda owner, accounts: saved.append(accounts))
    async with client_factory() as client:
        res = await client.get(
            "/api/notes/oauth/microsoft/callback?code=abc&state=forged",
            follow_redirects=False,
        )
    assert "tasks_oauth_error=invalid_state" in res.headers["location"]
    assert saved == []


@pytest.mark.asyncio
async def test_a_callback_with_no_code_is_refused(env):
    client_factory, _, _ = env
    async with client_factory() as client:
        res = await client.get("/api/notes/oauth/microsoft/callback",
                               follow_redirects=False)
    assert "tasks_oauth_error=missing_code" in res.headers["location"]


@pytest.mark.asyncio
async def test_a_refused_consent_reports_its_code(env):
    client_factory, _, _ = env
    async with client_factory() as client:
        res = await client.get(
            "/api/notes/oauth/microsoft/callback"
            "?error=access_denied&error_description=AADSTS65001%3A+no+consent",
            follow_redirects=False,
        )
    location = res.headers["location"]
    assert "tasks_oauth_error=microsoft_error" in location
    assert "AADSTS65001" in location


@pytest.mark.asyncio
async def test_a_grant_with_no_refresh_token_is_refused(env, monkeypatch):
    """Without offline_access there is nothing to refresh with, so the
    connection would die at the first token expiry."""
    client_factory, _, _ = env
    from routes.email_helpers import make_oauth_state

    saved = []
    monkeypatch.setattr(mt, "_save_mstodo_accounts",
                        lambda owner, accounts: saved.append(accounts))

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"access_token": "at", "expires_in": 3600}

    monkeypatch.setattr("httpx.post", lambda *a, **k: _Resp())
    state = make_oauth_state(str(uuid.uuid4()), USER)
    async with client_factory() as client:
        res = await client.get(
            f"/api/notes/oauth/microsoft/callback?code=abc&state={state}",
            follow_redirects=False,
        )
    assert "tasks_oauth_error=no_refresh_token" in res.headers["location"]
    assert saved == []
