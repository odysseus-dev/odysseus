"""Owner-scoped note routes must fail closed when the request has no identity.

The notes CRUD routes resolved the acting user with bare get_current_user().
A request that reached them carrying no identity (auth-middleware regression,
SSRF from a sibling service) therefore came through as user=None — and the
queries treat None as the single-user mode, i.e. blanket access to every
account's notes: list everything, read/update/delete/pin/archive any row,
reorder globally.

require_user() already encodes the correct policy — 401 when auth is
configured, while the documented anonymous modes (AUTH_ENABLED=false,
LOCALHOST_BYPASS on loopback, unconfigured first-run) still pass — and
fire-reminder in the same file already used it. The CRUD routes now resolve
the owner through it too.

Test transport note: these drive the ASGI app through ``httpx.ASGITransport``
+ ``httpx.AsyncClient`` rather than ``starlette.testclient.TestClient``.
TestClient runs the app inside a background event-loop thread spun up by
``anyio.from_thread.start_blocking_portal`` and then dispatches each sync
endpoint onto *another* worker thread; on some anyio/httpx/platform
combinations that two-thread handshake deadlocks and ``TestClient(app).get(...)``
simply hangs. ASGITransport runs the whole request on the test's own event
loop — no portal thread, no BaseHTTPMiddleware — so the suite is portable.
Identity is injected by a pure-ASGI shim that writes the same
``request.state`` fields the real auth middleware sets.
"""
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Note
import routes.note_routes as nr


# A deliberately NON-loopback peer. require_user has loopback fall-throughs
# (unconfigured first-run, LOCALHOST_BYPASS); pinning a public-looking client
# keeps every assertion below about the *configured-auth* path and not an
# accidental loopback bypass — the same reason the old fixture leaned on
# TestClient's non-loopback "testclient" host.
_PEER = ("203.0.113.7", 54321)


class _Identity:
    """Pure-ASGI shim mirroring what the auth middleware writes onto
    request.state. Pure-ASGI on purpose — it stays off Starlette's
    BaseHTTPMiddleware + sync-TestClient path, the source of the
    ``TestClient(app).get(...)`` hang. No x-test-user header => no identity,
    the exact state an auth-middleware regression would produce."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            state = scope.setdefault("state", {})
            user = headers.get(b"x-test-user")
            if user:
                state["current_user"] = user.decode()
            if headers.get(b"x-test-api-token"):
                state["current_user"] = "api"
                state["api_token"] = True
        await self.app(scope, receive, send)


def _temp_db(tmp_path):
    """Note routes over a fresh temp DB; returns the session factory."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'notes.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _build_app(factory, *, configured=True):
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=configured)
    app.include_router(nr.setup_note_routes())
    return _Identity(app)


def _client(app):
    """AsyncClient over the ASGI app with a non-loopback peer. Caller drives
    it inside ``async with``."""
    transport = httpx.ASGITransport(app=app, client=_PEER)
    return httpx.AsyncClient(transport=transport, base_url="http://notes.test")


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Configured-auth world: AUTH_ENABLED=true, auth_manager.is_configured,
    no LOCALHOST_BYPASS. Identity comes only from the x-test-user header
    (mirroring the auth middleware); no header => no identity, the exact state
    an auth-middleware regression leaves behind. Seeds one note each for alice
    and bob. Returns (app, factory)."""
    factory = _temp_db(tmp_path)
    monkeypatch.setattr(nr, "SessionLocal", factory)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)

    app = _build_app(factory)

    db = factory()
    db.add(Note(id="note-alice", owner="alice", title="a", content="x",
                items='[{"text": "t", "done": false}]'))
    db.add(Note(id="note-bob", owner="bob", title="b", content="y"))
    db.commit()
    db.close()
    return app, factory


async def test_no_identity_fails_closed_on_every_owner_scoped_route(env):
    app, _ = env
    async with _client(app) as c:
        assert (await c.get("/api/notes")).status_code == 401
        assert (await c.get("/api/notes/note-alice")).status_code == 401
        assert (await c.put("/api/notes/note-alice", json={"title": "pwn"})).status_code == 401
        assert (await c.delete("/api/notes/note-alice")).status_code == 401
        assert (await c.post("/api/notes/note-alice/pin")).status_code == 401
        assert (await c.post("/api/notes/note-alice/archive")).status_code == 401
        assert (await c.post("/api/notes/note-alice/items/0/toggle")).status_code == 401
        assert (await c.post("/api/notes/reorder", json={"ids": ["note-bob", "note-alice"]})).status_code == 401
        assert (await c.post("/api/notes", json={"title": "ghost"})).status_code == 401


async def test_no_identity_did_not_mutate_anything(env):
    app, factory = env
    async with _client(app) as c:
        await c.put("/api/notes/note-alice", json={"title": "pwn"})
        await c.post("/api/notes/note-alice/pin")
        await c.delete("/api/notes/note-bob")
    db = factory()
    rows = {n.id: n for n in db.query(Note).all()}
    db.close()
    assert set(rows) == {"note-alice", "note-bob"}
    assert rows["note-alice"].title == "a"
    assert not rows["note-alice"].pinned


async def test_authenticated_user_still_scoped_to_own_notes(env):
    app, _ = env
    alice = {"x-test-user": "alice"}
    async with _client(app) as c:
        listed = (await c.get("/api/notes", headers=alice)).json()["notes"]
        assert [n["id"] for n in listed] == ["note-alice"]
        assert (await c.get("/api/notes/note-alice", headers=alice)).status_code == 200
        # Someone else's note stays a 404 (don't reveal it exists).
        assert (await c.get("/api/notes/note-bob", headers=alice)).status_code == 404
        assert (await c.put("/api/notes/note-alice", json={"title": "mine"}, headers=alice)).status_code == 200


async def test_api_token_pseudo_user_is_rejected(env):
    """Bearer tokens must use the scope-aware API routes (require_user's
    existing contract), not slip into cookie-session routes as user 'api'."""
    app, _ = env
    async with _client(app) as c:
        r = await c.get("/api/notes", headers={"x-test-api-token": "1"})
    assert r.status_code == 403


async def test_auth_disabled_keeps_single_user_mode_working(monkeypatch, tmp_path):
    """AUTH_ENABLED=false is the operator's explicit anonymous mode: no
    identity must still mean full single-user access (issue #622 contract),
    even with a stale configured auth.json on disk."""
    factory = _temp_db(tmp_path)
    monkeypatch.setattr(nr, "SessionLocal", factory)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    app = _build_app(factory)

    db = factory()
    db.add(Note(id="n1", owner=None, title="solo", content="x"))
    db.commit()
    db.close()

    async with _client(app) as c:
        assert [n["id"] for n in (await c.get("/api/notes")).json()["notes"]] == ["n1"]
        assert (await c.put("/api/notes/n1", json={"title": "still mine"})).status_code == 200
        assert (await c.post("/api/notes/n1/pin")).status_code == 200
