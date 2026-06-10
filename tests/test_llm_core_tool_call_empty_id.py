"""OpenAI-compat streaming must give every tool call a non-empty id.

Some OpenAI-compatible servers stream delta.tool_calls without an `id`. The
accumulator initialized the slot as {"id": ""} and only filled it when the
provider sent one, so the call kept id="". agent_loop._append_tool_results
then set tool_call_id="" (the `tc.get("id", default)` default does NOT apply
to a present-but-empty value), and _sanitize_llm_messages — which builds the
expected id set only from truthy ids — dropped the entire tool round on the
next request. The Anthropic/Ollama paths synthesize ids; the OpenAI path did
not.
"""
import asyncio
import json

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


def _sse(delta):
    return "data: " + json.dumps({"choices": [{"delta": delta}]})


def _run(monkeypatch, lines):
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeClient(lines))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_mark_host_dead", lambda *a, **k: False, raising=False)

    async def go():
        out = []
        async for chunk in llm_core.stream_llm(
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "gpt-4o-test", [{"role": "user", "content": "hi"}],
            headers={"Authorization": "Bearer k"},
            tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        ):
            out.append(chunk)
        return "".join(out)

    blob = asyncio.run(go())
    for ln in blob.split("\n"):
        ln = ln.strip()
        if ln.startswith("data: ") and ln[6:] != "[DONE]":
            try:
                j = json.loads(ln[6:])
            except ValueError:
                continue
            if j.get("type") == "tool_calls":
                return j["calls"]
    return []


def test_idless_parallel_calls_get_distinct_nonempty_ids(monkeypatch):
    lines = [
        _sse({"tool_calls": [{"index": 0, "type": "function",
                              "function": {"name": "get_memory", "arguments": "{}"}}]}),
        _sse({"tool_calls": [{"index": 1, "type": "function",
                              "function": {"name": "bash", "arguments": "{}"}}]}),
        "data: [DONE]",
    ]
    calls = _run(monkeypatch, lines)
    assert len(calls) == 2
    ids = [c["id"] for c in calls]
    assert all(ids), f"empty tool_call id: {ids}"
    assert len(set(ids)) == 2, f"ids not distinct: {ids}"


def test_provider_supplied_id_is_preserved(monkeypatch):
    lines = [
        _sse({"tool_calls": [{"index": 0, "id": "call_abc", "type": "function",
                              "function": {"name": "x", "arguments": "{}"}}]}),
        "data: [DONE]",
    ]
    calls = _run(monkeypatch, lines)
    assert len(calls) == 1 and calls[0]["id"] == "call_abc"
