"""Tests for the fallback indicator in stream_llm_with_fallback.

When the selected model fails *before output* and another candidate answers,
a `fallback` event must be emitted so the switch is never masked under the
selected model's name (which is how a misconfigured provider can look like it
works while a different model silently answers).
"""
import json
import asyncio

import pytest

from src import llm_core


def _run_fallback(monkeypatch, per_model):
    """Drive stream_llm_with_fallback with a stubbed stream_llm that returns a
    canned SSE line list per candidate model. Returns the emitted chunks."""
    async def fake_stream(url, model, messages, **kw):
        for ln in per_model(model):
            yield ln
    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        out = []
        async for c in llm_core.stream_llm_with_fallback(
            [("u1", "primary", {}), ("u2", "backup", {})], [{"role": "user", "content": "hi"}]
        ):
            out.append(c)
        return out

    return asyncio.run(run())


def test_fallback_emits_indicator_when_primary_fails(monkeypatch):
    def per_model(model):
        if model == "primary":
            return ['event: error\ndata: {"status": 400, "text": "Provider X returned HTTP 400"}\n\n']
        return ['data: {"delta": "hello"}\n\n', "data: [DONE]\n\n"]
    chunks = _run_fallback(monkeypatch, per_model)
    fb = [json.loads(c[6:]) for c in chunks if c.startswith("data: ") and '"fallback"' in c]
    assert fb, f"no fallback event in {chunks}"
    assert fb[0]["type"] == "fallback"
    assert fb[0]["selected_model"] == "primary"
    assert fb[0]["answered_by"] == "backup"
    assert "400" in fb[0]["reason"]
    # the fallback notice must precede the answer content
    order = [i for i, c in enumerate(chunks) if '"fallback"' in c or '"delta": "hello"' in c]
    assert order == sorted(order)
    assert any('"delta": "hello"' in c for c in chunks)


def test_no_fallback_event_when_primary_succeeds(monkeypatch):
    def per_model(model):
        return ['data: {"delta": "ok"}\n\n', "data: [DONE]\n\n"]
    chunks = _run_fallback(monkeypatch, per_model)
    assert not any('"fallback"' in c for c in chunks)


def test_done_only_primary_invokes_fallback(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return ["data: [DONE]\n\n"]
        return [
            'data: {"type": "model_actual", "requested_model": "backup", "model": "backup-v2"}\n\n',
            'data: {"delta": "backup answer"}\n\n',
            "data: [DONE]\n\n",
        ]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary", "backup"]
    assert any('"delta": "backup answer"' in c for c in chunks)
    model_idx = next(i for i, c in enumerate(chunks) if '"model_actual"' in c)
    fallback_idx = next(i for i, c in enumerate(chunks) if '"fallback"' in c)
    answer_idx = next(i for i, c in enumerate(chunks) if '"delta": "backup answer"' in c)
    assert fallback_idx < model_idx < answer_idx


def test_usage_then_done_primary_invokes_fallback_and_discards_usage(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return [
                'data: {"type": "usage", "data": {"input_tokens": 4, "output_tokens": 0}}\n\n',
                "data: [DONE]\n\n",
            ]
        return ['data: {"delta": "backup answer"}\n\n', "data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary", "backup"]
    assert not any('"type": "usage"' in c for c in chunks)


@pytest.mark.parametrize(
    "output_chunk",
    [
        'data: {"delta": "visible text"}\n\n',
        'data: {"delta": "reasoning", "thinking": true}\n\n',
    ],
)
def test_text_or_reasoning_output_prevents_fallback(monkeypatch, output_chunk):
    calls = []

    def per_model(model):
        calls.append(model)
        return [output_chunk, "data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary"]
    assert output_chunk in chunks
    assert not any('"fallback"' in c for c in chunks)


def test_whitespace_only_delta_prevents_fallback(monkeypatch):
    calls = []
    whitespace = 'data: {"delta": "   "}\n\n'

    def per_model(model):
        calls.append(model)
        return [whitespace, "data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary"]
    assert whitespace in chunks
    assert not any('"fallback"' in c for c in chunks)


def test_completed_tool_call_output_prevents_fallback(monkeypatch):
    calls = []
    tool_calls = 'data: {"type": "tool_calls", "calls": [{"id": "c1", "name": "bash", "arguments": "{}"}]}\n\n'

    def per_model(model):
        calls.append(model)
        return [tool_calls, "data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary"]
    assert tool_calls in chunks
    assert not any('"fallback"' in c for c in chunks)


def test_tool_call_delta_is_forwarded_immediately_and_prevents_fallback(monkeypatch):
    calls = []
    advanced_past_delta = False
    tool_delta = 'data: {"type": "tool_call_delta", "index": 0, "arg_delta": "{\\"path\\":"}\n\n'
    tool_calls = 'data: {"type": "tool_calls", "calls": [{"id": "c1", "name": "write_file", "arguments": "{\\"path\\":\\"x\\"}"}]}\n\n'

    async def fake_stream(url, model, messages, **kw):
        nonlocal advanced_past_delta
        calls.append(model)
        yield tool_delta
        advanced_past_delta = True
        yield tool_calls
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        stream = llm_core.stream_llm_with_fallback(
            [("u1", "primary", {}), ("u2", "backup", {})],
            [{"role": "user", "content": "hi"}],
        )
        first = await anext(stream)
        assert first == tool_delta
        assert not advanced_past_delta
        chunks = [first]
        async for chunk in stream:
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())
    assert calls == ["primary"]
    assert tool_calls in chunks
    assert not any('"type": "fallback"' in c for c in chunks)


def test_empty_final_candidate_surfaces_terminal_error(monkeypatch):
    calls = []

    def per_model(model):
        calls.append(model)
        if model == "primary":
            return []  # clean EOF without substantive output
        return ["data: [DONE]\n\n"]

    chunks = _run_fallback(monkeypatch, per_model)
    assert calls == ["primary", "backup"]
    errors = [c for c in chunks if c.startswith("event: error")]
    assert len(errors) == 1
    assert "All model candidates returned no substantive output" in errors[0]
    assert '"status": 502' in errors[0]


def test_dedupe_candidates_keeps_first_of_each_route():
    """(url, model) is the route key; later repeats are dropped, order preserved,
    the first tuple (with its headers) kept, malformed entries filtered."""
    cands = [
        ("u1", "m1", {"h": 1}),   # first u1/m1 — kept
        ("u1", "m1", {"h": 2}),   # repeat route — dropped (first headers win)
        ("u2", "m2", {}),         # distinct — kept
        ("u1", "m1", {}),         # repeat again — dropped
        (None, "x", {}),          # malformed (no url) — dropped
        ("u3", "", {}),           # malformed (no model) — dropped
    ]
    assert llm_core._dedupe_candidates(cands) == [("u1", "m1", {"h": 1}), ("u2", "m2", {})]
    assert llm_core._dedupe_candidates([]) == []
    assert llm_core._dedupe_candidates(None) == []


def test_duplicate_route_is_attempted_only_once(monkeypatch):
    """A fallback that repeats the primary's (url, model) must NOT make the chain
    sail back into the same dead route — each distinct route is tried once."""
    calls = []

    async def fake_stream(url, model, messages, **kw):
        calls.append((url, model))
        yield 'event: error\ndata: {"status": 503, "text": "down"}\n\n'

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        out = []
        cands = [("u1", "m1", {}), ("u1", "m1", {}), ("u2", "m2", {})]
        async for c in llm_core.stream_llm_with_fallback(cands, [{"role": "user", "content": "hi"}]):
            out.append(c)
        return out

    asyncio.run(run())
    assert calls == [("u1", "m1"), ("u2", "m2")], f"duplicate route re-attempted: {calls}"


def test_summarize_stream_error():
    assert "400" in llm_core._summarize_stream_error('event: error\ndata: {"status": 400, "text": "nope"}\n\n')
    assert llm_core._summarize_stream_error(None) == "primary model failed"
    assert llm_core._summarize_stream_error("garbage") == "primary model failed"
