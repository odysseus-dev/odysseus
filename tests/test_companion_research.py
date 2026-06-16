"""Tests for the companion Deep Research launcher (/api/companion/research/*).

Covers the security-critical promises of the bridge:
  - a run/read is scoped to the token's REAL owner, never the pseudo-user "api"
  - cross-owner isolation: owner A can't see, cancel, or read owner B's runs
    (research_owns is a 404-not-403 gate)
  - /research/active returns only the caller's own running runs
  - resolve_research_endpoint refuses another owner's endpoint_id (no researching
    through a stranger's API key); a shared (null-owner) endpoint is allowed
  - session IDs are validated before they reach the handler or a file path
  - the research routes mount ONLY when a research_handler is provided
"""

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from companion.routes import (  # noqa: E402
    CompanionResearchStart,
    research_owns,
    resolve_research_endpoint,
    setup_companion_routes,
)


# --- fakes -----------------------------------------------------------------

class _FakeResearch:
    """Minimal stand-in for the app's research task manager."""

    def __init__(self, active=None, results=None, sources=None):
        self._active_tasks = active or {}
        self._results = results or {}
        self._sources = sources or {}
        self.started = []
        self.cancelled = []

    def get_result(self, sid):
        return self._results.get(sid)

    def get_sources(self, sid):
        return self._sources.get(sid, [])

    def cancel_research(self, sid):
        self.cancelled.append(sid)
        return True

    def start_research(self, **kwargs):
        self.started.append(kwargs)


def _req(owner="alice", api_token=True):
    # api_token=True routes token_owner() through api_token_owner — the real
    # owner the bridge must attribute to (not the sandboxed "api" pseudo-user).
    state = SimpleNamespace(api_token=api_token, api_token_owner=owner, current_user=owner)
    return SimpleNamespace(state=state)


def _route(router, suffix):
    for r in router.routes:
        if getattr(r, "path", "").endswith(suffix):
            return r
    raise AssertionError(f"route {suffix} not found")


# --- research_owns: the cross-owner gate -----------------------------------

def test_research_owns_in_memory_owner_match():
    h = _FakeResearch(active={"rp-1": {"owner": "alice", "status": "running"}})
    assert research_owns(h, "rp-1", "alice") is True
    assert research_owns(h, "rp-1", "bob") is False


def test_research_owns_unknown_session_is_false():
    # Not in memory and no on-disk JSON → not owned (caller gets a 404, never a
    # 403 that would confirm the run exists).
    assert research_owns(_FakeResearch(), "rp-missing", "alice") is False


# --- /research/active: only the caller's own running runs ------------------

def test_active_returns_only_callers_running_runs():
    h = _FakeResearch(active={
        "rp-a": {"owner": "alice", "status": "running", "query": "mine", "progress": {}, "started_at": 1},
        "rp-b": {"owner": "bob", "status": "running", "query": "theirs", "progress": {}, "started_at": 2},
        "rp-c": {"owner": "alice", "status": "done", "query": "finished", "progress": {}, "started_at": 3},
    })
    handler = _route(setup_companion_routes(research_handler=h), "/research/active").endpoint
    out = handler(_req(owner="alice"))
    sids = {r["session_id"] for r in out["active"]}
    assert sids == {"rp-a"}  # not bob's (cross-owner), not the done one


# --- cancel/result: validated + owner-gated --------------------------------

def test_cancel_rejects_bad_session_id():
    h = _FakeResearch()
    handler = _route(setup_companion_routes(research_handler=h), "/research/cancel/{session_id}").endpoint
    with pytest.raises(HTTPException) as exc:
        handler("../../etc/passwd", _req())
    assert exc.value.status_code == 400
    assert h.cancelled == []  # never reached the handler


def test_cancel_blocks_other_owners_run():
    h = _FakeResearch(active={"rp-1": {"owner": "bob", "status": "running"}})
    handler = _route(setup_companion_routes(research_handler=h), "/research/cancel/{session_id}").endpoint
    with pytest.raises(HTTPException) as exc:
        handler("rp-1", _req(owner="alice"))
    assert exc.value.status_code == 404
    assert h.cancelled == []


def test_result_reads_own_run():
    h = _FakeResearch(
        active={"rp-1": {"owner": "alice", "status": "done"}},
        results={"rp-1": "# Report"},
        sources={"rp-1": [{"url": "http://x", "title": "X"}]},
    )
    handler = _route(setup_companion_routes(research_handler=h), "/research/result/{session_id}").endpoint
    out = handler("rp-1", _req(owner="alice"))
    assert out["result"] == "# Report"
    assert out["sources"][0]["url"] == "http://x"


# --- resolve_research_endpoint: never research through a stranger's key -----

def _install_db_and_resolver(rows, monkeypatch):
    class _Q:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *a, **k):
            return self

        def first(self):
            return self._rows[0] if self._rows else None

    class _DB:
        def __init__(self, rows):
            self._rows = rows

        def query(self, *a, **k):
            return _Q(self._rows)

        def close(self):
            pass

    cd = types.ModuleType("core.database")
    cd.SessionLocal = lambda: _DB(rows)
    cd.ModelEndpoint = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", cd)

    er = types.ModuleType("src.endpoint_resolver")
    er.resolve_endpoint = lambda kind: ("", "", {})
    er.normalize_base = lambda b: b
    er.build_chat_url = lambda b: b + "/v1/chat/completions"
    er.build_headers = lambda key, b: {"Authorization": f"Bearer {key}"}
    monkeypatch.setitem(sys.modules, "src.endpoint_resolver", er)


def _endpoint(owner):
    return SimpleNamespace(
        id="ep1", is_enabled=True, owner=owner,
        base_url="http://host:1234", api_key="secret", cached_models='["m1"]',
    )


def test_resolve_rejects_other_owners_endpoint(monkeypatch):
    _install_db_and_resolver([_endpoint("bob")], monkeypatch)
    body = CompanionResearchStart(query="q", endpoint_id="ep1")
    with pytest.raises(HTTPException) as exc:
        resolve_research_endpoint(body, "alice")
    assert exc.value.status_code == 404


def test_resolve_accepts_own_and_shared_endpoint(monkeypatch):
    _install_db_and_resolver([_endpoint("alice")], monkeypatch)
    url, model, headers = resolve_research_endpoint(CompanionResearchStart(query="q", endpoint_id="ep1"), "alice")
    assert url.endswith("/v1/chat/completions")
    assert model == "m1"  # first cached model when none specified
    assert headers["Authorization"] == "Bearer secret"

    _install_db_and_resolver([_endpoint(None)], monkeypatch)  # legacy shared row
    url, _, _ = resolve_research_endpoint(CompanionResearchStart(query="q", endpoint_id="ep1"), "alice")
    assert url.endswith("/v1/chat/completions")


# --- mount only with a handler; correct verbs ------------------------------

def test_research_routes_absent_without_handler():
    paths = {getattr(r, "path", "") for r in setup_companion_routes().routes}
    assert not any("/research/" in p for p in paths)


def test_research_routes_present_and_verbs_correct():
    router = setup_companion_routes(research_handler=_FakeResearch())
    paths = {getattr(r, "path", "") for r in router.routes}
    assert "/api/companion/research/start" in paths
    assert "/api/companion/research/active" in paths
    assert set(_route(router, "/research/start").methods) == {"POST"}
    assert set(_route(router, "/research/active").methods) == {"GET"}
