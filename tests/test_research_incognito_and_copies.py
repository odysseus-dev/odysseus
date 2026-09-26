"""Incognito chats never get research posted back or copied; email copies escape raw HTML."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

import routes.chat_helpers as chat_helpers
import routes.research.research_routes as rr
from src import mail_copy


def _start_endpoint(monkeypatch, session_manager):
    import src.auth_helpers as auth_helpers
    monkeypatch.setattr(auth_helpers, "require_privilege", lambda request, priv: "alice")
    monkeypatch.setattr(rr, "resolve_endpoint", lambda role, owner=None: ("http://llm", "m", {}))
    handler = MagicMock()
    handler._active_tasks = {}
    router = rr.setup_research_routes(handler, session_manager=session_manager)
    ep = next(r.endpoint for r in router.routes
              if getattr(r, "path", "") == "/api/research/start" and "POST" in r.methods)
    return ep, handler


def _body(chat_sid):
    return SimpleNamespace(query="тема", max_rounds=0, search_provider=None, endpoint_id=None,
                           model=None, max_time=120, extraction_timeout=None,
                           extraction_concurrency=None, category=None, chat_session_id=chat_sid)


class _SM:
    def get_session(self, sid):
        return SimpleNamespace(owner="alice", model="m", history=[], add_message=lambda m: None)

    def save_sessions(self):
        pass


def test_start_skips_chat_delivery_for_incognito_chat(monkeypatch):
    monkeypatch.setitem(chat_helpers._INCOGNITO_CONTEXTS, "incog-1", {"messages": [], "updated_at": 9e18})
    ep, handler = _start_endpoint(monkeypatch, _SM())
    asyncio.run(ep(_body("incog-1"), SimpleNamespace(headers={})))
    assert handler.start_research.call_args.kwargs["on_complete"] is None


def test_start_wires_chat_delivery_for_normal_chat(monkeypatch):
    ep, handler = _start_endpoint(monkeypatch, _SM())
    asyncio.run(ep(_body("chat-1"), SimpleNamespace(headers={})))
    assert handler.start_research.call_args.kwargs["on_complete"] is not None


def test_trigger_research_tool_does_not_link_incognito_chat(monkeypatch):
    from src.tools.research import do_trigger_research
    monkeypatch.setitem(chat_helpers._INCOGNITO_CONTEXTS, "incog-2", {"messages": [], "updated_at": 9e18})
    seen = {}

    class _R:
        status_code = 200
        text = ""

        def json(self):
            return {"session_id": "rp-1"}

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None, headers=None):
            seen["json"] = json
            return _R()

    monkeypatch.setattr(httpx, "AsyncClient", _C)
    out = asyncio.run(do_trigger_research('{"topic": "t"}', owner="alice", session_id="incog-2"))
    assert "chat_session_id" not in seen["json"]
    assert "posted into this chat" not in out["output"]


def test_is_incognito_session():
    assert chat_helpers.is_incognito_session("") is False
    assert chat_helpers.is_incognito_session("nope-xyz") is False


def test_email_copy_escapes_raw_html_but_keeps_markdown():
    html = mail_copy._markdown_to_html('# T\n\n<a href="https://evil">click</a> **bold**\n\n> quote')
    assert "<a href" not in html and "&lt;a href" in html
    assert "<strong>bold</strong>" in html and "<blockquote>" in html
