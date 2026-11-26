"""Scoped todo bridge: sharing is refused whatever the token's scopes.

Sharing changes who can read a private note, so it is a human action in the
Notes window only (core.notes_sharing). The /api/codex/todos POST route
refuses `share`, `unshare` and `leave` for `todos:read` and `todos:write`
tokens alike, before anything reaches do_manage_notes; other actions still
pick their scope from WRITE_ACTIONS.
Driven through httpx.ASGITransport, as in tests/test_notes_fail_closed_auth.py.
"""
import httpx
import pytest
from fastapi import FastAPI

import routes.codex_routes as cr
from core.notes_sharing import AGENT_REFUSED_ACTIONS, SHARING_UI_ONLY_ERROR

_PEER = ("203.0.113.7", 54321)


class _Token:
    """Pure-ASGI shim writing what the bearer-token middleware sets."""

    def __init__(self, app, scopes):
        self.app = app
        self.scopes = list(scopes)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            state = scope.setdefault("state", {})
            state.update(current_user="api", api_token=True,
                         api_token_scopes=self.scopes, api_token_owner="alice")
        await self.app(scope, receive, send)


async def _post(monkeypatch, scopes, payload):
    calls = []

    async def _fake_manage_notes(content, owner=None):
        calls.append(owner)
        return {"ok": True}

    monkeypatch.setattr(cr, "do_manage_notes", _fake_manage_notes)
    app = FastAPI()
    app.include_router(cr.setup_codex_routes())
    transport = httpx.ASGITransport(app=_Token(app, scopes), client=_PEER)
    async with httpx.AsyncClient(transport=transport, base_url="http://codex.test") as c:
        r = await c.post("/api/codex/todos", json=payload)
    return r, calls


async def _get(scopes, path):
    app = FastAPI()
    app.include_router(cr.setup_codex_routes())
    transport = httpx.ASGITransport(app=_Token(app, scopes), client=_PEER)
    async with httpx.AsyncClient(transport=transport, base_url="http://codex.test") as c:
        return await c.get(path)


@pytest.mark.parametrize("scopes", [["todos:read"], ["todos:write"], ["todos:read", "todos:write"]])
@pytest.mark.parametrize("action", ["share", "unshare", "leave", "Share", "LEAVE"])
async def test_sharing_is_refused_for_every_todo_scope(monkeypatch, scopes, action):
    r, calls = await _post(monkeypatch, scopes, {"action": action, "id": "n1", "users": ["bob"], "permission": "edit"})
    assert r.status_code == 403
    assert r.json()["detail"] == SHARING_UI_ONLY_ERROR
    assert calls == [], "a sharing action must never reach do_manage_notes"


async def test_write_scope_still_writes(monkeypatch):
    r, calls = await _post(monkeypatch, ["todos:write"], {"action": "add", "title": "x"})
    assert r.status_code == 200
    assert calls == ["alice"], "write-scoped calls run as the token owner"


async def test_read_scope_cannot_write(monkeypatch):
    r, calls = await _post(monkeypatch, ["todos:read"], {"action": "add", "title": "x"})
    assert r.status_code == 403
    assert calls == []


async def test_read_scope_still_reads(monkeypatch):
    r, calls = await _post(monkeypatch, ["todos:read"], {"action": "get", "id": "n1"})
    assert r.status_code == 200
    assert calls == ["alice"]


def test_sharing_is_not_a_bridge_action():
    assert not cr.WRITE_ACTIONS & AGENT_REFUSED_ACTIONS
    assert AGENT_REFUSED_ACTIONS == {"share", "unshare", "leave"}


async def test_capabilities_do_not_list_sharing():
    r = await _get(["todos:write"], "/api/codex/capabilities")
    assert r.status_code == 200
    assert not set(r.json()["tools"]["todos"]["actions"]) & AGENT_REFUSED_ACTIONS


@pytest.mark.parametrize("inner", [
    {"action": "update", "id": "n1", "content": "x"},
    {"action": "add", "title": "x"},
    {"title": "x"},
])
async def test_body_envelope_is_a_clean_422(monkeypatch, inner):
    r, calls = await _post(monkeypatch, ["todos:write"], {"body": inner})
    assert r.status_code == 422
    assert '"body"' in r.json()["detail"]
    assert calls == [], "an enveloped payload must not reach do_manage_notes"
