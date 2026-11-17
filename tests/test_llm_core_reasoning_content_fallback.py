"""Regression tests for reasoning_content fallback in non-streaming paths.

Covers the five cases requested during PR review:
  1. llm_call (sync): content="" + reasoning_content="..." → returns reasoning text
  2. llm_call_async (async): same
  3. Normal content wins over reasoning_content when both present
  4. Streaming agent path: reasoning-only round does NOT emit the generic error
  5. Streaming agent path: reasoning tokens are NOT duplicated as normal answer text
"""
import asyncio
import json

import httpx
import pytest

from src import llm_core


# ---------------------------------------------------------------------------
# Helpers: fake httpx responses for the non-streaming llm_call* paths
# ---------------------------------------------------------------------------

def _sync_response(payload: dict) -> httpx.Response:
    req = httpx.Request("POST", "http://test/v1/chat/completions")
    return httpx.Response(200, request=req, json=payload)


def _openai_msg(content, reasoning_content=None):
    msg = {"content": content}
    if reasoning_content is not None:
        msg["reasoning_content"] = reasoning_content
    return {"choices": [{"message": msg}]}


# ---------------------------------------------------------------------------
# 1. llm_call (sync): empty content → falls back to reasoning_content
# ---------------------------------------------------------------------------

def test_llm_call_returns_reasoning_content_when_content_empty(monkeypatch):
    monkeypatch.setattr(
        llm_core.httpx, "post",
        lambda *a, **kw: _sync_response(_openai_msg("", "I reasoned through it")),
    )
    result = llm_core.llm_call(
        "http://test/v1", "qwen3-8b",
        [{"role": "user", "content": "think"}],
    )
    assert result == "I reasoned through it"


# ---------------------------------------------------------------------------
# 2. llm_call_async (async): empty content → falls back to reasoning_content
# ---------------------------------------------------------------------------

def test_llm_call_async_returns_reasoning_content_when_content_empty(monkeypatch):
    class _FakeAsyncClient:
        async def post(self, *a, **kw):
            req = httpx.Request("POST", "http://test-async/v1/chat/completions")
            return httpx.Response(200, request=req,
                                  json=_openai_msg("", "async reasoning text"))

    monkeypatch.setattr(llm_core, "_get_http_client",
                        lambda: _FakeAsyncClient())

    result = asyncio.run(llm_core.llm_call_async(
        "http://test-async/v1", "qwen3-8b",
        [{"role": "user", "content": "think"}],
    ))
    assert result == "async reasoning text"


# ---------------------------------------------------------------------------
# 3. Normal content takes priority over reasoning_content when both present
# ---------------------------------------------------------------------------

def test_llm_call_content_wins_over_reasoning_content(monkeypatch):
    monkeypatch.setattr(
        llm_core.httpx, "post",
        lambda *a, **kw: _sync_response(
            _openai_msg("Normal answer", "some reasoning")
        ),
    )
    result = llm_core.llm_call(
        "http://test/v1", "some-model",
        [{"role": "user", "content": "hi"}],
    )
    assert result == "Normal answer"


# ---------------------------------------------------------------------------
# Thinking off: reasoning never stands in for the answer
# ---------------------------------------------------------------------------

def test_llm_call_with_thinking_off_returns_empty_instead_of_reasoning(monkeypatch):
    monkeypatch.setattr(
        llm_core.httpx, "post",
        lambda *a, **kw: _sync_response(_openai_msg("", "* User message: ...")),
    )
    result = llm_core.llm_call(
        "http://test/v1", "qwen3-8b",
        [{"role": "user", "content": "extract"}],
        thinking_mode="off",
    )
    assert result == ""


def test_llm_call_async_with_thinking_off_returns_empty_instead_of_reasoning(monkeypatch):
    class _FakeAsyncClient:
        async def post(self, *a, **kw):
            req = httpx.Request("POST", "http://test-async/v1/chat/completions")
            return httpx.Response(200, request=req,
                                  json=_openai_msg("", "* User message: ..."))

    monkeypatch.setattr(llm_core, "_get_http_client",
                        lambda: _FakeAsyncClient())

    result = asyncio.run(llm_core.llm_call_async(
        "http://test-async/v1", "qwen3-8b",
        [{"role": "user", "content": "extract"}],
        thinking_mode="off",
    ))
    assert result == ""


def test_llm_call_with_thinking_off_drops_mistral_structured_thinking(monkeypatch):
    content = [
        {"type": "thinking", "thinking": [{"type": "text", "text": "reasoning"}]},
        {"type": "text", "text": "answer"},
    ]
    monkeypatch.setattr(
        llm_core.httpx, "post",
        lambda *a, **kw: _sync_response(_openai_msg(content)),
    )
    result = llm_core.llm_call(
        "http://test/v1", "some-model",
        [{"role": "user", "content": "extract"}],
        thinking_mode="off",
    )
    assert result == "answer"


# ---------------------------------------------------------------------------
# Streaming agent path tests (4 and 5)
# These import and test _empty_response_fallback — the real production helper
# extracted from stream_agent_loop.  If the fallback branch is reverted or
# changed, these tests will fail.
# ---------------------------------------------------------------------------

from src.agent_loop import _empty_response_fallback


# ---------------------------------------------------------------------------
# 4. Reasoning-only round: generic error is suppressed
# ---------------------------------------------------------------------------

def test_stream_agent_reasoning_only_does_not_emit_error():
    final_response, chunk = _empty_response_fallback(
        full_response="",
        round_reasoning="I reasoned carefully",
        tool_events=[],
    )
    assert chunk is None, "Must not emit any SSE chunk when reasoning is present"
    assert "The model returned an empty response" not in (chunk or "")
    assert final_response == "I reasoned carefully"


# ---------------------------------------------------------------------------
# 5. Reasoning tokens are NOT re-emitted as a normal answer delta
# ---------------------------------------------------------------------------

def test_stream_agent_reasoning_not_duplicated_as_normal_delta():
    reasoning_text = "my internal reasoning"
    _, chunk = _empty_response_fallback(
        full_response="",
        round_reasoning=reasoning_text,
        tool_events=[],
    )
    # chunk must be None — the reasoning was already sent as {thinking:true}
    assert chunk is None, (
        f"reasoning text was re-emitted as a normal delta chunk: {chunk!r}"
    )
