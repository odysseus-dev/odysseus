"""Streaming tool-call accumulation tests for the OpenAI-compatible path.

Regression for Gemini's OpenAI-compat layer, which (a) attaches an opaque
thought_signature in `extra_content` on the function-call delta and (b) omits
`index` on PARALLEL tool calls — every parallel delta arrives as index=None.
The accumulator must give each parallel call its own slot (otherwise they
collide into slot 0, overwriting the first call's name and concatenating —
corrupting — its arguments) and must preserve extra_content per call.
"""
import json
import asyncio

from src import llm_core


class _FakeResp:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return _FakeResp(self._lines)

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kw):
        return _FakeStreamCtx(self._lines)


def _drive(monkeypatch, lines, model="gemini-3.1-pro-preview-customtools"):
    """Run stream_llm against a canned SSE line list; return parsed events."""
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def run():
        events = []
        async for chunk in llm_core.stream_llm(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            model,
            [{"role": "user", "content": "hi"}],
            headers={"Authorization": "Bearer k"},
            tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        ):
            for ln in chunk.split("\n"):
                ln = ln.strip()
                if ln.startswith("data: ") and ln[6:] != "[DONE]":
                    try:
                        events.append(json.loads(ln[6:]))
                    except ValueError:
                        pass
        return events

    return asyncio.run(run())


def _sse(delta):
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


def test_parallel_calls_with_null_index_do_not_collide(monkeypatch):
    # Two parallel calls, each complete in one delta, both with index=None
    # (exactly what Gemini's OpenAI-compat layer emits). Only the first carries
    # a thought_signature.
    lines = [
        _sse({"tool_calls": [{
            "index": None, "id": "call_a", "type": "function",
            "function": {"name": "get_memory", "arguments": "{}"},
            "extra_content": {"google": {"thought_signature": "SIG0"}},
        }]}),
        _sse({"tool_calls": [{
            "index": None, "id": "call_b", "type": "function",
            "function": {"name": "bash", "arguments": '{"command":"echo hi"}'},
        }]}),
        "data: [DONE]",
    ]
    events = _drive(monkeypatch, lines)
    calls = next(e["calls"] for e in events if e.get("type") == "tool_calls")
    assert len(calls) == 2, f"parallel calls collided: {calls}"
    by_name = {c["name"]: c for c in calls}
    assert set(by_name) == {"get_memory", "bash"}
    # arguments are NOT corrupted by concatenation
    assert by_name["get_memory"]["arguments"] == "{}"
    assert by_name["bash"]["arguments"] == '{"command":"echo hi"}'
    # signature preserved on the first call only, exactly as received
    assert by_name["get_memory"]["extra_content"] == {"google": {"thought_signature": "SIG0"}}
    assert "extra_content" not in by_name["bash"]


def test_single_call_chunked_arguments_still_accumulate(monkeypatch):
    # Conformant OpenAI style: index present, arguments streamed in pieces.
    lines = [
        _sse({"tool_calls": [{"index": 0, "id": "c", "type": "function",
                              "function": {"name": "search", "arguments": '{"q":"'}}]}),
        _sse({"tool_calls": [{"index": 0, "function": {"arguments": 'cats"}'}}]}),
        "data: [DONE]",
    ]
    events = _drive(monkeypatch, lines, model="gpt-4o-test")
    calls = next(e["calls"] for e in events if e.get("type") == "tool_calls")
    assert len(calls) == 1
    assert calls[0]["name"] == "search"
    assert calls[0]["arguments"] == '{"q":"cats"}'


def test_null_index_chunked_arguments_attach_to_last_call(monkeypatch):
    # index=None where the name arrives first, then an arg-only continuation:
    # the continuation must attach to the just-started call, not open a new one.
    lines = [
        _sse({"tool_calls": [{"index": None, "id": "c", "type": "function",
                              "function": {"name": "search", "arguments": '{"q":'}}]}),
        _sse({"tool_calls": [{"index": None, "function": {"arguments": '"dogs"}'}}]}),
        "data: [DONE]",
    ]
    events = _drive(monkeypatch, lines)
    calls = next(e["calls"] for e in events if e.get("type") == "tool_calls")
    assert len(calls) == 1, f"continuation opened a spurious call: {calls}"
    assert calls[0]["arguments"] == '{"q":"dogs"}'


def test_sparse_integer_indices_then_null_do_not_collide(monkeypatch):
    # Hardening: a provider that uses sparse integer indices (0 and 2) and then
    # a null-index call must allocate ABOVE the max key, not at len()==2 (which
    # would overwrite slot 2). Three distinct calls must survive.
    lines = [
        _sse({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "f0", "arguments": "{}"}}]}),
        _sse({"tool_calls": [{"index": 2, "id": "b", "function": {"name": "f2", "arguments": "{}"}}]}),
        _sse({"tool_calls": [{"index": None, "id": "c", "function": {"name": "fn", "arguments": "{}"}}]}),
        "data: [DONE]",
    ]
    events = _drive(monkeypatch, lines)
    calls = next(e["calls"] for e in events if e.get("type") == "tool_calls")
    assert sorted(c["name"] for c in calls) == ["f0", "f2", "fn"], f"collision: {calls}"


def test_null_arguments_delta_does_not_drop_sibling_calls(monkeypatch):
    # A gateway can emit a tool_call delta whose `arguments` is JSON null. The
    # accumulator did `"" += None`, raising TypeError caught by the broad except
    # that wraps the whole chunk — so it abandoned the rest of the tool_calls
    # loop, silently dropping every LATER call in the same delta. Here the first
    # call has arguments: null; the second (same delta) must still survive.
    lines = [
        _sse({"tool_calls": [
            {"index": 0, "id": "a", "type": "function",
             "function": {"name": "first", "arguments": None}},
            {"index": 1, "id": "b", "type": "function",
             "function": {"name": "second", "arguments": "{}"}},
        ]}),
        "data: [DONE]",
    ]
    events = _drive(monkeypatch, lines, model="gpt-4o-test")
    calls = next(e["calls"] for e in events if e.get("type") == "tool_calls")
    assert sorted(c["name"] for c in calls) == ["first", "second"], calls
