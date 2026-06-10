"""Regression tests for the local-wrapper endpoint integration security pass.

These pin the fixes made while wiring a self-hosted, loopback OpenAI-compatible
wrapper endpoint into Odysseus:

  1. The shared CardDAV address book must not be reachable by non-admin
     owners through the contact agent tools (cross-owner contact leak).
  2. `adopt_served_model` must register the adopted server using the
     `base_url` key that `do_manage_endpoints` actually reads — passing the
     old `endpoint_url` key silently failed with "base_url is required".
  3. `resolve_session_auth` must not pull another owner's endpoint key.

`tests/conftest.py` puts the repo root on sys.path and stubs heavy optional
deps; `src.tool_implementations` imports cleanly under that harness.
"""

import json
import sys
from types import SimpleNamespace
import pytest


# ---------------------------------------------------------------------------
# Fake httpx client so the tested coroutines never touch the network.
# ---------------------------------------------------------------------------
class _FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = {} if payload is None else payload
        self.headers = {"content-type": "application/json"}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    """Async-context httpx.AsyncClient stand-in with canned responses."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **k):
        if "shell/exec" in url:
            # tmux has-session check + health curl both go through here.
            return _FakeResp(payload={"exit_code": 0, "stdout": '{"data": []}'})
        return _FakeResp(payload={})  # cookbook state write

    async def get(self, url, **k):
        if "cookbook/state" in url:
            return _FakeResp(payload={"tasks": []})
        return _FakeResp(payload={})


# ---------------------------------------------------------------------------
# Contacts: shared CardDAV book is admin / single-user only.
# ---------------------------------------------------------------------------
async def test_manage_contact_blocks_non_admin(monkeypatch):
    import src.tool_security as ts
    import src.tool_implementations as t

    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda o: False)
    res = await t.do_manage_contact(json.dumps({"action": "list"}), owner="bob")
    assert "admin" in (res.get("error") or "").lower()


async def test_manage_contact_allows_admin(monkeypatch):
    import src.tool_security as ts
    import src.tool_implementations as t
    from routes import contacts_routes as cc

    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda o: True)
    monkeypatch.setattr(cc, "_fetch_contacts", lambda *a, **k: [])
    res = await t.do_manage_contact(json.dumps({"action": "list"}), owner="admin")
    # Admin path proceeds into the contacts helpers instead of the gate error.
    assert "admin" not in (res.get("error") or "").lower()


async def test_resolve_contact_skips_carddav_for_non_admin(monkeypatch):
    import httpx
    import src.tool_security as ts
    import src.tool_implementations as t
    from routes import contacts_routes as cc

    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda o: False)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(cc, "_fetch_contacts", _spy)
    await t.do_resolve_contact(json.dumps({"name": "alice"}), owner="bob")
    assert called["n"] == 0, "non-admin resolve_contact must not read the shared CardDAV book"


async def test_resolve_contact_reads_carddav_for_admin(monkeypatch):
    import httpx
    import src.tool_security as ts
    import src.tool_implementations as t
    from routes import contacts_routes as cc

    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda o: True)
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return []

    monkeypatch.setattr(cc, "_fetch_contacts", _spy)
    await t.do_resolve_contact(json.dumps({"name": "alice"}), owner="admin")
    assert called["n"] == 1, "admin resolve_contact should consult the CardDAV book"


# ---------------------------------------------------------------------------
# adopt_served_model registers the endpoint with `base_url`, not `endpoint_url`.
# ---------------------------------------------------------------------------
async def test_manage_endpoints_existing_add_updates_local_metadata(monkeypatch):
    import core.database as dbmod
    import routes.model_routes as mr
    import src.tool_security as ts
    import src.tool_implementations as t

    monkeypatch.setattr(ts, "owner_is_admin_or_single_user", lambda o: True)
    invalidations = []
    monkeypatch.setattr(mr, "invalidate_model_endpoint_caches", lambda: invalidations.append("cleared"))

    existing = SimpleNamespace(
        id="abc123",
        name="Local Wrapper",
        base_url="http://127.0.0.1:8000/v1",
        cached_models=json.dumps(["local-model"]),
        supports_tools=None,
        diagnostics_paths=None,
        owner=None,
    )
    commits = {"n": 0}

    class _Query:
        # Dedupe now resolves the existing row owner-scoped and key-aware via
        # find_endpoint_for_dedupe, i.e. .filter().filter().order_by().all().
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return [existing]

        def first(self):
            return existing

    class _DB:
        def query(self, model):
            return _Query()

        def commit(self):
            commits["n"] += 1

        def close(self):
            pass

    monkeypatch.setattr(dbmod, "SessionLocal", lambda: _DB())

    res = await t.do_manage_endpoints(
        json.dumps({
            "action": "add",
            "base_url": "http://127.0.0.1:8000/v1",
            "supports_tools": False,
            "diagnostics_paths": {"health": "/health"},
            "skip_probe": True,
        }),
        owner="admin",
    )

    assert res.get("existing") is True
    assert res.get("supports_tools") is False
    assert res.get("diagnostics_sections") == ["health"]
    assert existing.supports_tools is False
    assert json.loads(existing.diagnostics_paths) == {"health": "/health"}
    assert commits["n"] == 1
    assert invalidations == ["cleared"]


def test_find_endpoint_for_dedupe_is_owner_scoped_and_key_aware(monkeypatch):
    """The shared dedupe helper (used by both the admin add route and the
    manage_endpoints tool) is owner-scoped AND key-aware. The tool previously
    matched base_url alone, so it could return another owner's row and could
    never add a second credential for the same provider URL."""
    import routes.model_routes as mr

    # Minimal SQLAlchemy-expression stand-ins so the helper's real query runs.
    class _Pred:
        def __init__(self, fn):
            self.fn = fn

        def __or__(self, other):
            return _Pred(lambda r: self.fn(r) or other.fn(r))

    class _Col:
        def __init__(self, name):
            self.name = name

        def __eq__(self, value):
            return _Pred(lambda r: getattr(r, self.name, None) == value)

        def is_(self, value):
            return _Pred(lambda r: getattr(r, self.name, None) is value)

        def desc(self):
            return self

    class _FakeME:
        base_url = _Col("base_url")
        owner = _Col("owner")

    class _Query:
        def __init__(self, rows):
            self.rows = list(rows)

        def filter(self, *preds):
            self.rows = [r for r in self.rows if all(p.fn(r) for p in preds)]
            return self

        def order_by(self, *args):
            self.rows.sort(key=lambda r: r.owner is None)  # owned before shared
            return self

        def all(self):
            return list(self.rows)

    class _DB:
        def __init__(self, rows):
            self.rows = rows

        def query(self, model):
            assert model is _FakeME
            return _Query(self.rows)

    monkeypatch.setattr(mr, "ModelEndpoint", _FakeME)
    URL = "http://127.0.0.1:8000/v1"

    def ep(owner, key):
        return SimpleNamespace(base_url=URL, owner=owner, api_key=key)

    # Same URL + same key → reuse the row.
    keyed = ep("admin", "sk-a")
    assert mr.find_endpoint_for_dedupe(_DB([keyed]), URL, "admin", "sk-a") is keyed
    # Same URL + a DIFFERENT non-empty key → no match (a new credential row).
    assert mr.find_endpoint_for_dedupe(_DB([keyed]), URL, "admin", "sk-b") is None
    # Same URL + incoming key fills a key-less existing row.
    keyless = ep("admin", None)
    assert mr.find_endpoint_for_dedupe(_DB([keyless]), URL, "admin", "sk-b") is keyless
    # Owner scope: another user's private row is invisible → no match.
    assert mr.find_endpoint_for_dedupe(_DB([ep("bob", "sk-a")]), URL, "alice", "sk-a") is None
    # Legacy null-owner shared row is visible to anyone.
    shared = ep(None, "sk-a")
    assert mr.find_endpoint_for_dedupe(_DB([shared]), URL, "alice", "sk-a") is shared


async def test_adopt_served_model_registers_with_base_url(monkeypatch):
    import httpx
    import src.tool_implementations as t

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    captured = {}

    async def _fake_manage_endpoints(content, owner=None):
        captured["content"] = content
        captured["owner"] = owner
        return {"response": "Added", "endpoint_id": "abc123"}

    # do_adopt_served_model does `from src.tool_implementations import
    # do_manage_endpoints` at call time, so patching the module attr works.
    monkeypatch.setattr(t, "do_manage_endpoints", _fake_manage_endpoints)

    res = await t.do_adopt_served_model(
        json.dumps({"tmux_session": "srv1", "model": "org/Model", "port": 8000}),
        owner="admin",
    )
    assert res.get("exit_code") == 0, res
    assert "content" in captured, "adopt should have called do_manage_endpoints"
    payload = json.loads(captured["content"])
    assert payload.get("base_url") == "http://localhost:8000/v1"
    assert "endpoint_url" not in payload, "must use base_url, not the ignored endpoint_url key"
    assert payload.get("action") == "add"


# ---------------------------------------------------------------------------
# Session auth resolution must not pull another owner's endpoint key.
# ---------------------------------------------------------------------------
def _fresh_chat_helpers():
    # Another test module may have installed an incomplete `core.database` stub
    # at collection time. Complete the stub in place so chat_helpers can import
    # the attributes it evaluates without splitting SQLAlchemy model identity for
    # the rest of the pytest process.
    _db = sys.modules.get("core.database")
    if _db is not None:
        from unittest.mock import MagicMock

        for name in ("SessionLocal", "Session", "ModelEndpoint"):
            if not hasattr(_db, name):
                setattr(_db, name, MagicMock())

        _model_endpoint = _db.ModelEndpoint
        for attr in ("is_enabled", "base_url"):
            if not hasattr(_model_endpoint, attr):
                setattr(_model_endpoint, attr, object())

        _session_model = _db.Session
        for attr in ("id", "owner"):
            if not hasattr(_session_model, attr):
                setattr(_session_model, attr, object())

    for name in ("routes.chat_helpers", "src.context_compactor", "src.endpoint_resolver"):
        sys.modules.pop(name, None)
    import routes.chat_helpers as ch
    return ch


def test_resolve_session_auth_does_not_cross_owner(monkeypatch):
    from types import SimpleNamespace

    ch = _fresh_chat_helpers()
    import src.auth_helpers as ah

    endpoints = [
        SimpleNamespace(
            base_url="http://127.0.0.1:8000/v1",
            api_key="BOB_SECRET",
            name="Bob Local",
            owner="bob",
        )
    ]
    updates = []

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return self.rows

        def update(self, values):
            updates.append(values)
            return 1

        def only_owner(self, owner):
            self.rows = [r for r in self.rows if getattr(r, "owner", None) in (owner, None)]
            return self

    class _DB:
        def query(self, model):
            if model is ch.ModelEndpoint:
                return _Query(list(endpoints))
            return _Query([])

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(ch, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(ah, "owner_filter", lambda q, model, owner: q.only_owner(owner))

    sess = SimpleNamespace(endpoint_url="http://127.0.0.1:8000/v1/chat/completions", headers={})
    ch.resolve_session_auth(sess, "sid", owner="alice")

    assert sess.headers == {}
    assert updates == []


def test_resolve_session_auth_persists_visible_endpoint_key(monkeypatch):
    from types import SimpleNamespace

    ch = _fresh_chat_helpers()
    import src.auth_helpers as ah

    endpoints = [
        SimpleNamespace(
            base_url="http://127.0.0.1:8000/v1",
            api_key="SHARED_SECRET",
            name="Shared Local",
            owner=None,
        )
    ]
    updates = []

    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return self.rows

        def update(self, values):
            updates.append(values)
            return 1

        def only_owner(self, owner):
            self.rows = [r for r in self.rows if getattr(r, "owner", None) in (owner, None)]
            return self

    class _DB:
        def query(self, model):
            if model is ch.ModelEndpoint:
                return _Query(list(endpoints))
            return _Query([])

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(ch, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(ah, "owner_filter", lambda q, model, owner: q.only_owner(owner))

    sess = SimpleNamespace(endpoint_url="http://127.0.0.1:8000/v1/chat/completions", headers={})
    ch.resolve_session_auth(sess, "sid", owner="alice")

    assert sess.headers == {"Authorization": "Bearer SHARED_SECRET"}
    assert updates == [{"headers": {"Authorization": "Bearer SHARED_SECRET"}}]


def test_email_tag_owner_clause_keeps_legacy_rows_single_user_only(monkeypatch):
    import routes.email_routes as er

    monkeypatch.setattr(
        er,
        "_email_tag_owner_aliases",
        lambda account_id, owner="": ["alice", "alice@example.com"] if owner else [""],
    )

    clause, params = er._email_tag_owner_clause(None, "alice")
    assert params == ["alice", "alice@example.com"]
    assert "owner IN (?,?)" in clause
    assert "owner IS NULL" not in clause

    clause, params = er._email_tag_owner_clause(None, "")
    assert params == [""]
    assert "owner IS NULL" in clause


def test_email_list_tag_queries_use_shared_owner_clause():
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "routes" / "email_routes.py"
    body = src.read_text(encoding="utf-8").split("def _list_emails_sync", 1)[1].split("@router.get(\"/list\")", 1)[0]

    assert "_email_tag_owner_clause(account_id, owner)" in body
    assert "OR owner IS NULL" not in body
