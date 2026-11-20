"""The notes router on a disposable database, driven as a signed-in account.

`notes_routes_harness(monkeypatch, tmp_path)` mounts `setup_note_routes()`
on a bare FastAPI app (auth on, upload reservation stubbed) and yields
helpers: `seed` adds one of alice's notes, `content`
reads a row back, `write_between` commits a competing change from another
session the first time a route folds legacy columns (every write route does
that between its read and its commit), and `call` sends a request as a user.
Driven through httpx.ASGITransport like tests/test_notes_markdown_routes.py.
"""
import datetime
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
from fastapi import FastAPI

import core.database as cdb
from core.database import Note
import routes.note_routes as nr
from tests.helpers.database import disposable_database

_PEER = ("203.0.113.7", 54321)


class _Identity:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            user = dict(scope.get("headers") or []).get(b"x-test-user")
            if user:
                scope.setdefault("state", {})["current_user"] = user.decode()
        await self.app(scope, receive, send)


@contextmanager
def notes_routes_harness(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.delenv("LOCALHOST_BYPASS", raising=False)
    with disposable_database(tmp_path) as factory:
        monkeypatch.setattr(nr, "SessionLocal", factory)
        monkeypatch.setattr(cdb, "SessionLocal", factory)
        monkeypatch.setattr(nr, "reserve_upload_references", lambda *a, **kw: None)
        app = FastAPI()
        app.state.auth_manager = SimpleNamespace(is_configured=True)
        app.include_router(nr.setup_note_routes())

        def seed(note_id, content, owner="alice"):
            with factory() as db:
                db.add(Note(id=note_id, owner=owner, title=note_id, content=content))
                db.commit()
                return db.get(Note, note_id).updated_at.isoformat()

        def content(note_id):
            with factory() as db:
                return db.get(Note, note_id).content

        def write_between(note_id, change):
            original = nr._fold_legacy_columns
            fired = []

            def fold(note):
                if not fired:
                    fired.append(True)
                    with factory() as other:
                        row = other.get(Note, note_id)
                        row.content = change(row.content)
                        row.updated_at = row.updated_at + datetime.timedelta(seconds=1)
                        other.commit()
                return original(note)

            monkeypatch.setattr(nr, "_fold_legacy_columns", fold)
            return fired

        async def call(method, url, user="alice", **kw):
            transport = httpx.ASGITransport(app=_Identity(app), client=_PEER)
            async with httpx.AsyncClient(transport=transport, base_url="http://notes.test") as c:
                return await c.request(method, url, headers={"x-test-user": user}, **kw)

        yield SimpleNamespace(seed=seed, content=content, write_between=write_between, call=call)
