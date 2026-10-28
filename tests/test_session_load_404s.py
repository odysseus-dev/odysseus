"""Session-load 404 regression (passover item #9).

Loading a chat session used to fire two benign 404s in the browser console:
  GET /api/research/status/<id>      (no research for this session)
  GET /api/chat/stream_status/<id>   (no active stream for this session)

Both are now a normal "empty" state: HTTP 200 with an explicit status
("none" / "idle"). The clients already branch on the body status, so this is
backward compatible (an older server's 404 is still tolerated).
"""
import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

from routes import chat_routes
from routes import research_routes


def _route_endpoint(router, path, method="GET"):
    for route in router.routes:
        if getattr(route, "path", "") != path:
            continue
        if method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"{method} {path} not registered")


def _req(user="alice"):
    return SimpleNamespace(state=SimpleNamespace(current_user=user))


# --- /api/research/status/<id> -------------------------------------------

def test_research_status_returns_200_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(research_routes, "DEEP_RESEARCH_DIR", str(tmp_path / "deep_research"))
    handler = MagicMock()
    handler._active_tasks = {}
    router = research_routes.setup_research_routes(handler)
    endpoint = _route_endpoint(router, "/api/research/status/{session_id}")

    out = asyncio.run(endpoint(session_id="sess-abc", request=_req()))

    assert out == {"status": "none"}


def test_research_status_returns_status_when_present(tmp_path, monkeypatch):
    monkeypatch.setattr(research_routes, "DEEP_RESEARCH_DIR", str(tmp_path / "deep_research"))
    handler = MagicMock()
    handler._active_tasks = {"sess-abc": {"owner": "alice"}}
    handler.get_status.return_value = {"status": "running", "progress": {"phase": "searching"}}
    handler.get_source_state.return_value = None
    handler.get_source_coverage.return_value = None
    handler.get_navigation_trace.return_value = None
    handler.get_action_trace.return_value = None
    router = research_routes.setup_research_routes(handler)
    endpoint = _route_endpoint(router, "/api/research/status/{session_id}")

    out = asyncio.run(endpoint(session_id="sess-abc", request=_req()))

    assert out["status"] == "running"


# --- /api/chat/stream_status/<id> ----------------------------------------

def _chat_stream_status_endpoint(monkeypatch):
    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda request, session_id: None)
    router = chat_routes.setup_chat_routes(
        MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
    )
    return _route_endpoint(router, "/api/chat/stream_status/{session_id}")


def test_stream_status_returns_200_idle_when_no_stream(monkeypatch):
    endpoint = _chat_stream_status_endpoint(monkeypatch)
    chat_routes._active_streams.pop("sess-idle", None)

    out = asyncio.run(endpoint(SimpleNamespace(), "sess-idle"))

    assert out == {"status": "idle"}


def test_stream_status_returns_record_when_streaming(monkeypatch):
    endpoint = _chat_stream_status_endpoint(monkeypatch)
    chat_routes._active_streams["sess-live"] = {"status": "streaming", "mode": "agent"}
    try:
        out = asyncio.run(endpoint(SimpleNamespace(), "sess-live"))
    finally:
        chat_routes._active_streams.pop("sess-live", None)

    assert out["status"] == "streaming"


# --- client compatibility (source-level) ---------------------------------

def test_chat_watchdog_accepts_200_idle_body():
    src = inspect.getsource(chat_routes)  # ensure chat_routes imported
    chatjs = open("static/js/chat.js", encoding="utf-8").read()
    # The stale-stream watchdog must key off the body status, not only a 404.
    assert "_noActiveStream" in chatjs
    assert "_probe.status !== 'streaming'" in chatjs
    assert src  # keep flake happy


def test_sessions_check_uses_body_status_not_ok_only():
    sj = open("static/js/sessions.js", encoding="utf-8").read()
    assert "info.status !== 'streaming'" in sj
    assert "status !== 'streaming'" in sj
