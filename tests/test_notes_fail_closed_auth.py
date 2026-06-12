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
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
from core.database import Note
import routes.note_routes as nr


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Note routes over a temp DB. Identity comes from the x-test-user header
    (mirroring what the auth middleware sets); no header → no identity, the
    exact state an auth-middleware regression would produce. The TestClient's
    client.host is "testclient" (not loopback), so loopback fall-throughs in
    require_user stay out of the picture."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'notes.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(nr, "SessionLocal", factory)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request, call_next):
        user = request.headers.get("x-test-user")
        if user:
            request.state.current_user = user
        if request.headers.get("x-test-api-token"):
            request.state.current_user = "api"
            request.state.api_token = True
        return await call_next(request)

    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(nr.setup_note_routes())

    db = factory()
    db.add(Note(id="note-alice", owner="alice", title="a", content="x",
                items='[{"text": "t", "done": false}]'))
    db.add(Note(id="note-bob", owner="bob", title="b", content="y"))
    db.commit()
    db.close()
    return TestClient(app), factory


def test_no_identity_fails_closed_on_every_owner_scoped_route(client):
    c, _ = client
    assert c.get("/api/notes").status_code == 401
    assert c.get("/api/notes/note-alice").status_code == 401
    assert c.put("/api/notes/note-alice", json={"title": "pwn"}).status_code == 401
    assert c.delete("/api/notes/note-alice").status_code == 401
    assert c.post("/api/notes/note-alice/pin").status_code == 401
    assert c.post("/api/notes/note-alice/archive").status_code == 401
    assert c.post("/api/notes/note-alice/items/0/toggle").status_code == 401
    assert c.post("/api/notes/reorder", json={"ids": ["note-bob", "note-alice"]}).status_code == 401
    assert c.post("/api/notes", json={"title": "ghost"}).status_code == 401


def test_no_identity_did_not_mutate_anything(client):
    c, factory = client
    c.put("/api/notes/note-alice", json={"title": "pwn"})
    c.post("/api/notes/note-alice/pin")
    c.delete("/api/notes/note-bob")
    db = factory()
    rows = {n.id: n for n in db.query(Note).all()}
    db.close()
    assert set(rows) == {"note-alice", "note-bob"}
    assert rows["note-alice"].title == "a"
    assert not rows["note-alice"].pinned


def test_authenticated_user_still_scoped_to_own_notes(client):
    c, _ = client
    alice = {"x-test-user": "alice"}
    listed = c.get("/api/notes", headers=alice).json()["notes"]
    assert [n["id"] for n in listed] == ["note-alice"]
    assert c.get("/api/notes/note-alice", headers=alice).status_code == 200
    # Someone else's note stays a 404 (don't reveal it exists).
    assert c.get("/api/notes/note-bob", headers=alice).status_code == 404
    assert c.put("/api/notes/note-alice", json={"title": "mine"}, headers=alice).status_code == 200


def test_api_token_pseudo_user_is_rejected(client):
    """Bearer tokens must use the scope-aware API routes (require_user's
    existing contract), not slip into cookie-session routes as user 'api'."""
    c, _ = client
    r = c.get("/api/notes", headers={"x-test-api-token": "1"})
    assert r.status_code == 403


def test_auth_disabled_keeps_single_user_mode_working(monkeypatch, tmp_path):
    """AUTH_ENABLED=false is the operator's explicit anonymous mode: no
    identity must still mean full single-user access (issue #622 contract),
    even with a stale configured auth.json on disk."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'notes.db'}",
        connect_args={"check_same_thread": False},
        poolclass=NullPool,
    )
    cdb.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(nr, "SessionLocal", factory)
    monkeypatch.setenv("AUTH_ENABLED", "false")

    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(nr.setup_note_routes())

    db = factory()
    db.add(Note(id="n1", owner=None, title="solo", content="x"))
    db.commit()
    db.close()

    c = TestClient(app)
    assert [n["id"] for n in c.get("/api/notes").json()["notes"]] == ["n1"]
    assert c.put("/api/notes/n1", json={"title": "still mine"}).status_code == 200
    assert c.post("/api/notes/n1/pin").status_code == 200
