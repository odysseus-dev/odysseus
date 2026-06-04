"""Memory routes must owner-scope caller-supplied session ids.

SessionManager.get_session returns any session by id (no owner scoping). The
/api/memory extract, audit, import, and by-session handlers accept a
caller-supplied session id, so without an ownership gate a user could target
another tenant's session and leak their chat history, session-scoped LLM
credentials, or session title.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

import routes.memory_routes as mr


def _route(router, path, method):
    for r in router.routes:
        if r.path == path and method in getattr(r, "methods", set()):
            return r.endpoint
    raise AssertionError(path)


def _router(monkeypatch, caller):
    monkeypatch.setattr(mr, "get_current_user", lambda request: caller, raising=False)
    monkeypatch.setattr(mr, "require_user", lambda request: caller, raising=False)
    sm = MagicMock()
    sm.sessions = {}
    sm.get_session = lambda sid: SimpleNamespace(
        owner="alice", name="Secret project", endpoint_url="http://x", model="m",
        headers={"Authorization": "Bearer victim-secret"},
        get_context_messages=lambda: [],
    )
    mem = MagicMock()
    mem.load = lambda owner=None: []
    return mr.setup_memory_routes(mem, sm)


def test_extract_rejects_other_users_session(monkeypatch):
    router = _router(monkeypatch, caller="bob")
    extract = _route(router, "/api/memory/extract", "POST")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(extract(request=None, session="alice-sess"))
    assert exc.value.status_code == 404


def test_by_session_rejects_other_users_session(monkeypatch):
    router = _router(monkeypatch, caller="bob")
    gbs = _route(router, "/api/memory/by-session/{session_id}", "GET")
    with pytest.raises(HTTPException) as exc:
        gbs(request=None, session_id="alice-sess")
    assert exc.value.status_code == 404


def test_owner_can_access_own_session(monkeypatch):
    router = _router(monkeypatch, caller="alice")
    gbs = _route(router, "/api/memory/by-session/{session_id}", "GET")
    out = gbs(request=None, session_id="alice-sess")
    assert out["session_name"] == "Secret project"
