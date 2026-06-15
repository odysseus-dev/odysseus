"""POST /api/sessions/import — create a new owned session from an exported payload.

Import is the missing half of the existing JSON export (GET /session/{sid}/export
?fmt=json). These tests pin the security-critical contract: a fresh session id is
always minted, the caller is always stamped as owner (the file's id/owner are
never trusted), messages round-trip through the DB, and malformed payloads are
rejected with 400 instead of silently dropping data.
"""
import asyncio
import sys
import types

import pytest
from fastapi import HTTPException

from tests.helpers.sqlite_db import make_temp_sqlite

import core.database as cdb
from core.database import Session as DbSession

_TS, _ENGINE, _TMPDB = make_temp_sqlite(cdb.Base.metadata)


@pytest.fixture(autouse=True)
def _isolate_session_routes():
    """Keep these tests hermetic.

    ``routes.session_routes.router`` is a module-level APIRouter, so every
    ``setup_session_routes`` call *appends* routes to it. Without restoring it,
    a later test that looks a route up by path (e.g. ``next(r for r in
    router.routes ...)``) would grab the route this test registered first.
    Snapshot the router and the temp DB, and restore both afterwards.
    """
    import routes.session_routes as sr
    routes_before = list(sr.router.routes)
    yield
    sr.router.routes[:] = routes_before
    db = _TS()
    try:
        from core.database import ChatMessage as _DbMsg
        db.query(_DbMsg).delete()
        db.query(DbSession).delete()
        db.commit()
    finally:
        db.close()


def _stub_multipart_if_missing(monkeypatch):
    try:
        import python_multipart  # noqa: F401
        return
    except ImportError:
        pass
    stub = types.ModuleType("python_multipart")
    stub.__version__ = "0.0.20"
    monkeypatch.setitem(sys.modules, "python_multipart", stub)


class _Req:
    """Minimal Request stand-in with an async json() body."""
    def __init__(self, payload, raise_on_json=False):
        self._payload = payload
        self._raise = raise_on_json

    async def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._payload


def _build(monkeypatch, user="import-tester-7f3a"):
    import core.session_manager as csm
    import routes.session_routes as sr

    _stub_multipart_if_missing(monkeypatch)
    monkeypatch.setattr(cdb, "SessionLocal", _TS)
    monkeypatch.setattr(csm, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "SessionLocal", _TS)
    monkeypatch.setattr(sr, "effective_user", lambda request: user)

    sm = csm.SessionManager()
    router = sr.setup_session_routes(sm, {})
    endpoint = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", "") == "/api/sessions/import"
        and "POST" in getattr(r, "methods", set())
    )
    return csm, sm, endpoint


def test_import_creates_owned_session_and_round_trips(monkeypatch):
    csm, sm, endpoint = _build(monkeypatch)
    owner = "import-tester-7f3a"
    payload = {
        "name": "My chat",
        "model": "gpt-4o",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello there"},
        ],
        # Hostile fields that must be ignored — never trust the file:
        "owner": "mallory",
        "id": "attacker-supplied-id",
    }
    result = asyncio.run(endpoint(_Req(payload)))

    new_id = result["id"]
    assert result["count"] == 2
    assert new_id != "attacker-supplied-id"

    # DB row is owned by the caller, not the file's "owner".
    db = _TS()
    try:
        row = db.query(DbSession).filter(DbSession.id == new_id).first()
        assert row is not None
        assert row.owner == owner
        assert row.name == "My chat"
        assert row.model == "gpt-4o"
    finally:
        db.close()

    # Full round-trip: a fresh manager hydrates the messages from the DB.
    fresh = csm.SessionManager()
    s = fresh.get_session(new_id)
    assert s.owner == owner
    assert [(m.role, m.content) for m in s.history] == [
        ("user", "hi"),
        ("assistant", "hello there"),
    ]


def test_import_defaults_name_when_missing(monkeypatch):
    _csm, _sm, endpoint = _build(monkeypatch)
    result = asyncio.run(endpoint(_Req({"messages": [{"role": "user", "content": "x"}]})))
    assert result["name"] == "Imported conversation"
    assert result["count"] == 1


def test_import_allows_empty_conversation(monkeypatch):
    _csm, _sm, endpoint = _build(monkeypatch)
    result = asyncio.run(endpoint(_Req({"name": "empty", "messages": []})))
    assert result["count"] == 0


@pytest.mark.parametrize("payload", [
    ["not", "a", "dict"],                                  # top-level not an object
    {"name": "x"},                                          # no messages array
    {"messages": "nope"},                                  # messages not a list
    {"messages": ["not-an-object"]},                       # message not an object
    {"messages": [{"role": "user"}]},                      # content missing
    {"messages": [{"role": "user", "content": 123}]},      # content wrong type
    {"messages": [{"role": "hacker", "content": "x"}]},    # disallowed role
])
def test_import_rejects_malformed_payloads(monkeypatch, payload):
    _csm, _sm, endpoint = _build(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(_Req(payload)))
    assert exc.value.status_code == 400


def test_import_rejects_invalid_json(monkeypatch):
    _csm, _sm, endpoint = _build(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(_Req(None, raise_on_json=True)))
    assert exc.value.status_code == 400


def test_import_rejects_too_many_messages(monkeypatch):
    _csm, _sm, endpoint = _build(monkeypatch)
    payload = {"messages": [{"role": "user", "content": "x"}] * 5001}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint(_Req(payload)))
    assert exc.value.status_code == 400
