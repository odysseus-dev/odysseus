"""Tests for the fallback indicator in stream_llm_with_fallback.

When the selected model fails *before output* and another candidate answers,
a `fallback` event must be emitted so the switch is never masked under the
selected model's name (which is how a misconfigured provider can look like it
works while a different model silently answers).
"""
import json
import asyncio

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
