"""Tests for the Clicky -> Odysseus agent-loop voice bridge."""

from __future__ import annotations

import asyncio
import json

import pytest

from clicky_integration import clicky_chat as cc


def _body(prompt="what's on my screen"):
    return {"messages": [{"role": "user", "content": prompt}], "max_tokens": 512}


async def _collect(agen):
    return [chunk async for chunk in agen]


def _delta_text(sse: str) -> str:
    assert sse.startswith("data: ")
    payload = json.loads(sse[6:])
    assert payload["type"] == "content_block_delta"
    return payload["delta"]["text"]


# ── translate_agent_chunk (pure) ──

def test_text_delta_becomes_anthropic_delta():
    out = cc.translate_agent_chunk('data: {"delta": "hello there"}\n\n')
    assert _delta_text(out) == "hello there"


def test_thinking_delta_is_swallowed():
    assert cc.translate_agent_chunk('data: {"delta": "reasoning", "thinking": true}\n\n') is None


def test_tool_events_are_swallowed():
    for ev in ("tool_start", "tool_output", "agent_step", "metrics", "model_actual"):
        chunk = f'data: {{"type": "{ev}", "tool": "screen_look"}}\n\n'
        assert cc.translate_agent_chunk(chunk) is None, ev


def test_done_returns_sentinel():
    assert cc.translate_agent_chunk("data: [DONE]\n\n") == cc._DONE_SENTINEL


def test_error_event_is_spoken():
    out = cc.translate_agent_chunk('event: error\ndata: {"error": "model exploded"}\n\n')
    assert "model exploded" in _delta_text(out)


def test_error_type_is_spoken():
    out = cc.translate_agent_chunk('data: {"type": "error", "message": "boom"}\n\n')
    assert "boom" in _delta_text(out)


def test_ask_user_question_is_spoken():
    out = cc.translate_agent_chunk('data: {"type": "ask_user", "question": "Approve the click?"}\n\n')
    assert "Approve the click?" in _delta_text(out)


def test_non_data_and_bad_json_swallowed():
    assert cc.translate_agent_chunk("event: ping\n\n") is None
    assert cc.translate_agent_chunk("data: not-json\n\n") is None


# ── stream_agent_chat (integration, mocked loop) ──

def _fake_loop_factory(chunks, captured):
    async def fake_loop(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        for c in chunks:
            yield c
    return fake_loop


def test_stream_agent_chat_speaks_text_swallows_machinery(monkeypatch):
    chunks = [
        'data: {"delta": "Checking your screen. "}\n\n',
        'data: {"type": "tool_start", "tool": "screen_look"}\n\n',
        'data: {"type": "tool_output", "tool": "screen_look"}\n\n',
        'data: {"delta": "I see VS Code."}\n\n',
        'data: {"type": "metrics", "data": {}}\n\n',
        "data: [DONE]\n\n",
    ]
    captured = {}
    monkeypatch.setattr("src.agent_loop.stream_agent_loop", _fake_loop_factory(chunks, captured))
    monkeypatch.setattr(cc, "resolve_clicky_endpoint", lambda: ("http://x/v1", "m", None))

    out = asyncio.run(_collect(cc.stream_agent_chat(_body())))

    spoken = "".join(_delta_text(c) for c in out if c.startswith("data: ") and c != "data: [DONE]\n\n")
    assert spoken == "Checking your screen. I see VS Code."
    assert out[-1] == "data: [DONE]\n\n"
    # No tool machinery leaked into the spoken stream.
    assert not any("tool_start" in c or "metrics" in c for c in out)


def test_stream_agent_chat_scopes_operator_tools(monkeypatch):
    captured = {}
    monkeypatch.setattr("src.agent_loop.stream_agent_loop", _fake_loop_factory(["data: [DONE]\n\n"], captured))
    monkeypatch.setattr(cc, "resolve_clicky_endpoint", lambda: ("http://x/v1", "m", None))

    asyncio.run(_collect(cc.stream_agent_chat(_body())))

    relevant = captured["kwargs"]["relevant_tools"]
    assert {"screen_look", "browser_act", "desktop_act", "operator_research"} <= relevant
    assert captured["kwargs"]["session_id"] == "clicky-voice"
    assert isinstance(captured["kwargs"]["max_rounds"], int)


def test_stream_agent_chat_no_endpoint(monkeypatch):
    monkeypatch.setattr(cc, "resolve_clicky_endpoint", lambda: (None, None, None))
    out = asyncio.run(_collect(cc.stream_agent_chat(_body())))
    joined = "".join(out)
    assert "endpoint" in joined.lower()
    assert out[-1] == "data: [DONE]\n\n"


# ── consent pre-grant behind the flag ──

def test_consent_pre_granted_when_flag_on(monkeypatch):
    captured = {}
    monkeypatch.setattr("src.agent_loop.stream_agent_loop", _fake_loop_factory(["data: [DONE]\n\n"], captured))
    monkeypatch.setattr(cc, "resolve_clicky_endpoint", lambda: ("http://x/v1", "m", None))
    monkeypatch.setenv("CLICKY_OPERATOR_CONSENT", "true")

    granted = []
    monkeypatch.setattr("services.operator.core.grant_consent", lambda sid: granted.append(sid))

    asyncio.run(_collect(cc.stream_agent_chat(_body())))
    assert granted == ["clicky-voice"]


def test_consent_not_granted_when_flag_off(monkeypatch):
    captured = {}
    monkeypatch.setattr("src.agent_loop.stream_agent_loop", _fake_loop_factory(["data: [DONE]\n\n"], captured))
    monkeypatch.setattr(cc, "resolve_clicky_endpoint", lambda: ("http://x/v1", "m", None))
    monkeypatch.delenv("CLICKY_OPERATOR_CONSENT", raising=False)

    granted = []
    monkeypatch.setattr("services.operator.core.grant_consent", lambda sid: granted.append(sid))

    asyncio.run(_collect(cc.stream_agent_chat(_body())))
    assert granted == []


# ── mode dispatch ──

def test_stream_clicky_chat_dispatches_agent_mode(monkeypatch):
    monkeypatch.setenv("CLICKY_CHAT_MODE", "agent")

    called = {}

    async def fake_agent(body):
        called["body"] = body
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(cc, "stream_agent_chat", fake_agent)
    out = asyncio.run(_collect(cc.stream_clicky_chat(_body("click submit"))))
    assert called["body"]["messages"][0]["content"] == "click submit"
    assert out == ["data: [DONE]\n\n"]


def test_stream_clicky_chat_endpoint_mode_unchanged(monkeypatch):
    monkeypatch.setenv("CLICKY_CHAT_MODE", "endpoint")

    agent_called = {"n": 0}

    async def fake_agent(body):
        agent_called["n"] += 1
        yield "x"

    async def fake_endpoint(body):
        yield "data: endpoint\n\n"

    monkeypatch.setattr(cc, "stream_agent_chat", fake_agent)
    monkeypatch.setattr(cc, "stream_endpoint_chat", fake_endpoint)
    out = asyncio.run(_collect(cc.stream_clicky_chat(_body())))
    assert agent_called["n"] == 0
    assert out == ["data: endpoint\n\n"]
