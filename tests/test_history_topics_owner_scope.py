"""
Round-4 / Finding A3.1 validator.

Claim under test:
    /api/conversations/topics (routes/history_routes.py:478-485) forwards
    `owner=get_current_user(request)` to `analyze_topics`, and
    `analyze_topics` in src/topic_analyzer.py:21-85 SKIPS the owner
    filter when `owner` is falsy. Combined with the
    LOCALHOST_BYPASS / trusted-loopback branch in app.py:248, an
    unauthenticated loopback caller can aggregate topic counts and
    per-snippet `session_id` / `session_name` / `role` / `snippet`
    examples from every user's sessions.

This test pins the data flow by:

  (1) Calling `analyze_topics` directly with `owner=None` against a
      stub SessionManager whose `sessions` dict contains entries for
      three different owners. A correctly-scoped helper MUST return
      zero topics (or an empty result) when owner is None/empty,
      because no caller has identified themselves.

  (2) Driving the actual route through FastAPI's TestClient with an
      AuthMiddleware stub that mimics the LOCALHOST_BYPASS path: the
      request has no auth cookie, no bearer token, no internal-tool
      header, but the middleware short-circuits BEFORE setting
      `request.state.current_user`. The expected behavior is one of:
          (a) 401 / 403 response, OR
          (b) a response that only contains the requesting user's
              topics (which for this anonymous caller is none).

If the test FAILS, the bug is REAL. If the test PASSES, the claim
is a FALSE POSITIVE.
"""
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(sid, owner, history):
    """Build a dict-shaped session that `analyze_topics` can walk."""
    return {
        "id": sid,
        "owner": owner,
        "name": f"Session {sid[:6]}",
        "archived": False,
        "history": history,
    }


def _stub_session_manager(sessions):
    """A duck-typed SessionManager exposing the `.sessions` dict the
    `analyze_topics` helper iterates over."""
    return SimpleNamespace(sessions=sessions)


# ---------------------------------------------------------------------------
# 1. Pure-function test on `analyze_topics`
# ---------------------------------------------------------------------------


def test_analyze_topics_with_owner_none_does_not_leak_across_owners():
    """
    The most important invariant: when no caller is identified (owner is
    None/empty), `analyze_topics` MUST return no cross-tenant data. The
    current implementation (src/topic_analyzer.py:21-39) only enters the
    owner filter when `owner` is truthy, so owner=None silently scans
    every session regardless of owner.

    This is a stand-alone unit test of the helper. If it returns topics
    for sessions whose owners are "alice", "bob", and "carol" while
    `owner=None`, the filter is not strict, and the route bug is real.
    """
    from src.topic_analyzer import analyze_topics

    sessions = {
        "s-alice-1": _make_session(
            "s-alice-1", "alice",
            [{"role": "user", "content": "Let's discuss AI safety."}],
        ),
        "s-bob-1": _make_session(
            "s-bob-1", "bob",
            [{"role": "user", "content": "I need to fix a python bug today."}],
        ),
        "s-carol-1": _make_session(
            "s-carol-1", "carol",
            [{"role": "user", "content": "Family dinner planning and health."}],
        ),
    }
    sm = _stub_session_manager(sessions)

    result = analyze_topics(sm, owner=None)

    # When the caller is unidentified, no cross-tenant topics may leak.
    assert result["topics"] == [], (
        f"analyze_topics(owner=None) leaked cross-tenant data: "
        f"{[t['topic'] for t in result['topics']]}. "
        f"Expected empty result so an unauthenticated loopback caller "
        f"cannot aggregate other users' topic frequencies."
    )
    assert result["total_topics"] == 0, (
        f"analyze_topics(owner=None) reported total_topics="
        f"{result['total_topics']} instead of 0. Cross-tenant leakage."
    )


def test_analyze_topics_with_owner_none_no_owner_attribute_session_also_safe():
    """
    Even if some legacy sessions have NO `owner` key at all (pre-ownership
    data, or sessions created before multi-tenant), the helper must NOT
    surface them to an unauthenticated caller. The current code's
    `if owner:` short-circuit means those rows ARE included in the
    no-owner scan. This test pins that the leak is observable on the
    data path that the route will hit.
    """
    from src.topic_analyzer import analyze_topics

    # Legacy-shape session: no `owner` key, ownerless topic-rich history.
    legacy = _make_session(
        "s-legacy-1", None,
        [{"role": "user", "content": "Work meeting about a project deadline."}],
    )
    del legacy["owner"]  # truly ownerless dict
    sm = _stub_session_manager({"s-legacy-1": legacy})

    result = analyze_topics(sm, owner=None)

    assert result["topics"] == [], (
        f"analyze_topics(owner=None) returned topics for an ownerless "
        f"session: {result['topics']}. An anonymous caller should not be "
        f"able to harvest topics from any session they don't own."
    )


# ---------------------------------------------------------------------------
# 2. End-to-end test through FastAPI TestClient with a stubbed
#    AuthMiddleware that simulates the LOCALHOST_BYPASS branch.
# ---------------------------------------------------------------------------


def _build_app_with_loopback_bypass(session_manager):
    """
    Build a minimal FastAPI app that:
      * mounts the real `setup_history_routes(session_manager)` router,
      * installs a stub `AuthMiddleware` whose `dispatch` reproduces
        the LOCALHOST_BYPASS branch from app.py:248-249 (return from
        dispatch *before* setting `request.state.current_user`),
      * uses an `AuthManager` whose `is_configured` is True so the
        non-loopback / non-bypass path would otherwise 401.

    The result: the middleware trusts the request as loopback-bypass
    but leaves `request.state.current_user` unset. The route then
    reads `get_current_user(request)` -> None, which `analyze_topics`
    treats as 'no filter' and returns cross-tenant topics.
    """
    from fastapi import FastAPI
    from routes.history_routes import setup_history_routes

    app = FastAPI()
    app.include_router(setup_history_routes(session_manager))

    # Stub AuthManager so app.state.auth_manager.is_configured is True.
    auth_mgr = MagicMock()
    auth_mgr.is_configured = True
    auth_mgr.users = {"alice": {}, "bob": {}, "carol": {}}
    app.state.auth_manager = auth_mgr

    # Stub BaseHTTPMiddleware that mirrors the loopback-bypass branch.
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as _Req

    class LoopbackBypassMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # Faithful reproduction of the LOCALHOST_BYPASS branch:
            # `if LOCALHOST_BYPASS and _is_trusted_loopback(request):
            #      return await call_next(request)`
            # No `request.state.current_user = ...` is set.
            return await call_next(request)

    # Re-register as "AuthMiddleware" to mirror the prod class name and
    # make the contract obvious to the reader.
    class AuthMiddleware(LoopbackBypassMiddleware):
        pass

    app.add_middleware(AuthMiddleware)
    return app


def test_route_rejects_or_scopes_under_loopback_bypass():
    """
    Drive the real route via TestClient with a stubbed AuthMiddleware
    that mimics LOCALHOST_BYPASS: no `current_user` is set. The
    endpoint must NOT return cross-tenant topics in the response.
    """
    from fastapi.testclient import TestClient

    sessions = {
        "s-alice-1": _make_session(
            "s-alice-1", "alice",
            [{"role": "user", "content": "AI safety is a fascinating topic."}],
        ),
        "s-bob-1": _make_session(
            "s-bob-1", "bob",
            [{"role": "user", "content": "I need to fix a python bug."}],
        ),
        "s-carol-1": _make_session(
            "s-carol-1", "carol",
            [{"role": "user", "content": "Family dinner planning tonight."}],
        ),
    }
    sm = _stub_session_manager(sessions)
    app = _build_app_with_loopback_bypass(sm)
    client = TestClient(app)

    # No auth cookie, no bearer token, no internal-tool header. Pretend
    # to come from a real local client. The middleware bypasses auth
    # exactly as app.py:248 would.
    resp = client.get(
        "/api/conversations/topics",
        headers={"host": "127.0.0.1:8000"},
    )

    # Behavior under the fix: the route uses `require_user` which raises
    # 401 when auth_manager is configured and the caller is anonymous,
    # which is the state this test sets up. The cross-tenant leak path
    # (200 with topics from other owners) must be closed.
    assert resp.status_code == 401, (
        f"Expected 401 from /api/conversations/topics under the loopback "
        f"bypass + configured auth_manager; got {resp.status_code}. "
        f"body={resp.text!r}"
    )


def test_route_data_flow_on_paper():
    """
    White-box check: prove the data flow on the page.
    - `get_current_user(request)` returns `None` when no state is set.
    - `analyze_topics(sm, owner=None)` walks sessions of all owners.
    - The route forwards `owner=user` (where user may be None) to
      `analyze_topics` without further checks.
    This test does not exercise the route; it pins the three independent
    facts the audit relies on. If any of them regresses (e.g. someone
    adds a fallback in get_current_user, or changes `if owner:` to a
    strict bool check), this test will start failing in a way that
    makes the regression visible.
    """
    from src.auth_helpers import get_current_user
    from src.topic_analyzer import analyze_topics

    # (a) get_current_user with no state returns None.
    req = SimpleNamespace(state=SimpleNamespace())
    assert get_current_user(req) is None, (
        "get_current_user must return None when no middleware has set "
        "request.state.current_user."
    )

    # (b) analyze_topics with owner=None MUST NOT walk other owners'
    # sessions. The previous behavior was a cross-tenant data leak; the
    # fix returns an empty result. If this assertion is inverted in a
    # future regression, A3.1 is back.
    sm = _stub_session_manager({
        "s1": _make_session("s1", "alice",
                            [{"role": "user", "content": "AI safety."}]),
        "s2": _make_session("s2", "bob",
                            [{"role": "user", "content": "Python bug."}]),
    })
    res = analyze_topics(sm, owner=None)
    assert res["topics"] == [], (
        "analyze_topics(owner=None) returned cross-tenant data — "
        "Finding A3.1 regression. Expected empty result."
    )
    assert res["total_topics"] == 0
