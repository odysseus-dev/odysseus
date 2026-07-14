"""Tests for operator perception (screen_look) and recall (screen_recall)."""

from __future__ import annotations

import asyncio
import json
import socket
from unittest.mock import patch
from urllib import error as urlerror

import pytest


class _FakeResp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _frames_payload(*texts, app="Code", window="editor"):
    return {
        "data": [
            {
                "type": "OCR",
                "content": {
                    "text": t,
                    "timestamp": f"2026-07-14T05:00:{i:02d}Z",
                    "app_name": app,
                    "window_name": window,
                },
            }
            for i, t in enumerate(texts)
        ]
    }


@pytest.fixture(autouse=True)
def _capability_available():
    with patch("services.operator.perception.require_capability", return_value=None):
        with patch("services.operator.recall.require_capability", return_value=None):
            yield


# ── screen_look ──

def test_screen_look_live_view_defaults_to_60s():
    from services.operator import perception

    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _FakeResp(_frames_payload("hello world"))

    with patch.object(perception.request, "urlopen", fake_urlopen):
        result = perception.screen_look()

    assert result["ok"] is True
    assert result["capability"] == "screen_perception"
    assert "content_type=ocr" in seen["url"]
    assert "q=" not in seen["url"]
    assert result["data"]["lookback_minutes"] == 1.0
    assert result["data"]["frames"][0]["text"] == "hello world"
    assert result["data"]["window_count"] == 1


def test_screen_look_query_uses_lookback_and_q_param():
    from services.operator import perception

    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        return _FakeResp(_frames_payload("TypeError: x is undefined"))

    with patch.object(perception.request, "urlopen", fake_urlopen):
        result = perception.screen_look(query="TypeError", minutes=30)

    assert "q=TypeError" in seen["url"]
    assert result["data"]["lookback_minutes"] == 30.0
    assert result["data"]["query"] == "TypeError"


def test_screen_look_clamps_lookback_to_max():
    from services.operator import perception

    with patch.object(perception.request, "urlopen", lambda req, timeout=None: _FakeResp(_frames_payload())):
        result = perception.screen_look(query="x", minutes=9999)
    assert result["data"]["lookback_minutes"] == 120.0


def test_screen_look_truncates_at_frame_boundary():
    from services.operator import perception

    payload = _frames_payload("a" * 60, "b" * 60, "c" * 60)
    with patch.dict("os.environ", {"OPERATOR_PERCEPTION_CHAR_BUDGET": "100"}):
        with patch.object(perception.request, "urlopen", lambda req, timeout=None: _FakeResp(payload)):
            result = perception.screen_look(query="x")

    data = result["data"]
    assert data["truncated"] is True
    assert data["omitted_frames"] >= 1
    assert "frame boundary" in data["truncation_note"]
    # No frame text was cut mid-string.
    assert all(len(f["text"]) == 60 for f in data["frames"])


def test_screen_look_degrades_on_connection_error():
    from services.operator import perception

    def boom(req, timeout=None):
        raise urlerror.URLError("connection refused")

    with patch.object(perception.request, "urlopen", boom):
        result = perception.screen_look()

    assert result["ok"] is False
    assert result["degraded"] is True
    assert "screenpipe_error" in result["reason"]


def test_screen_look_respects_capability_gate():
    from services.operator import perception
    from services.operator.core import degraded_envelope

    gate = degraded_envelope("screen_perception", "screen_perception_offline")
    with patch("services.operator.perception.require_capability", return_value=gate):
        result = perception.screen_look()
    assert result is gate


# ── screen_recall ──

def test_screen_recall_happy_path_includes_cross_store_sections():
    from services.operator import recall

    payload = {
        "visual_results": [{"id": "tile1", "tile_metadata": {"window_title": "Stripe"}}],
        "agent_memory_results": [{"summary": "invoice work"}],
        "notes_results": [],
    }
    with patch.object(recall.request, "urlopen", lambda req, timeout=None: _FakeResp(payload)):
        result = recall.screen_recall("stripe dashboard", k=5)

    assert result["ok"] is True
    data = result["data"]
    assert data["visual_results"][0]["tile_metadata"]["window_title"] == "Stripe"
    assert data["agent_memory_results"][0]["summary"] == "invoice work"
    assert data["notes_results"] == []


def test_screen_recall_timeout_is_structured():
    from services.operator import recall

    def slow(req, timeout=None):
        raise urlerror.URLError(socket.timeout("timed out"))

    with patch.object(recall.request, "urlopen", slow):
        result = recall.screen_recall("anything")

    assert result["ok"] is False
    assert result["reason"] == "timeout"
    assert "shorter query" in result["hint"]


def test_screen_recall_no_index_degrades_with_hint():
    from services.operator import recall

    payload = {"error": "no FAISS index found", "visual_results": []}
    with patch.object(recall.request, "urlopen", lambda req, timeout=None: _FakeResp(payload)):
        result = recall.screen_recall("anything")

    assert result["reason"] == "no_index"
    assert "start_pixelrag_local" in result["hint"]


def test_screen_recall_rejects_empty_query():
    from services.operator.recall import screen_recall

    result = screen_recall("   ")
    assert result["ok"] is False
    assert result["reason"] == "empty_query"


def test_screen_recall_clamps_k():
    from services.operator import recall

    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"visual_results": []})

    with patch.object(recall.request, "urlopen", fake_urlopen):
        recall.screen_recall("q", k=999)
    assert seen["payload"]["k"] == 20


# ── tool registration wiring ──

def test_tools_registered_in_schemas_and_tags():
    from src.agent_tools import TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {t["function"]["name"] for t in FUNCTION_TOOL_SCHEMAS}
    assert {"screen_look", "screen_recall"} <= names
    assert {"screen_look", "screen_recall"} <= TOOL_TAGS


def test_keyword_hint_surfaces_screen_tools():
    from src.tool_index import ToolIndex

    message = "what's on my screen right now?"
    hinted = set()
    for keywords, tools in ToolIndex._KEYWORD_HINTS.items():
        if any(kw in message.lower() for kw in keywords):
            hinted |= tools
    assert "screen_look" in hinted


def test_do_screen_look_parses_json_args():
    from src.tool_implementations import do_screen_look

    captured = {}

    def fake_look(query=None, minutes=None):
        captured["query"] = query
        captured["minutes"] = minutes
        return {"ok": True, "capability": "screen_perception", "data": {}, "degraded": False}

    with patch("services.operator.perception.screen_look", fake_look):
        result = asyncio.run(do_screen_look('{"query": "error", "minutes": 10}'))

    assert result["ok"] is True
    assert captured == {"query": "error", "minutes": 10}


def test_do_screen_recall_requires_query():
    from src.tool_implementations import do_screen_recall

    result = asyncio.run(do_screen_recall("{}"))
    assert result["exit_code"] == 1
    assert "query" in result["error"]
