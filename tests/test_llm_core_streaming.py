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
    def __init__(self, lines, delay=0):
        self._lines = lines
        self._delay = delay
        self.status_code = 200

    async def aiter_lines(self):
        for ln in self._lines:
            if self._delay:
                await asyncio.sleep(self._delay)
            yield ln

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, lines, delay=0):
        self._lines = lines
        self._delay = delay

    async def __aenter__(self):
        return _FakeResp(self._lines, self._delay)

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, lines):
        self._lines = lines

    def stream(self, method, url, **kw):
        return _FakeStreamCtx(self._lines)


class _FakeResponsesStreamClient:
    def __init__(self, lines, seen, delay=0):
        self._lines = lines
        self.seen = seen
        self.delay = delay

    def stream(self, method, url, **kw):
        self.seen["method"] = method
        self.seen["url"] = url
        self.seen["json"] = kw.get("json")
        return _FakeStreamCtx(self._lines, self.delay)


class _FakeResponsesClient:
    def __init__(self, payload, seen):
        self._payload = payload
        self.seen = seen

    def stream(self, method, url, **kw):
        self.seen["method"] = method
        self.seen["url"] = url
        self.seen["json"] = kw.get("json")
        lines = ["data: " + json.dumps({"type": "response.completed", "response": self._payload})]
        return _FakeStreamCtx(lines)

    async def post(self, url, **kw):
        self.seen["url"] = url
        self.seen["json"] = kw.get("json")
        request = llm_core.httpx.Request("POST", url)
        return llm_core.httpx.Response(200, request=request, json=self._payload)


class _SlowResponsesClient(_FakeResponsesClient):
    def __init__(self, payload, seen, delay=0.03):
        super().__init__(payload, seen)
        self.delay = delay

    async def post(self, url, **kw):
        await asyncio.sleep(self.delay)
        return await super().post(url, **kw)

    def stream(self, method, url, **kw):
        self.seen["method"] = method
        self.seen["url"] = url
        self.seen["json"] = kw.get("json")
        lines = ["data: " + json.dumps({"type": "response.completed", "response": self._payload})]
        return _FakeStreamCtx(lines, self.delay)


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


def _responses_sse(payload):
    return "data: " + json.dumps(payload)


def test_gpt55_pro_uses_responses_api_bridge(monkeypatch):
    llm_core._response_cache.clear()
    seen = {}
    payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "OK from pro"}],
            }
        ],
        "usage": {"input_tokens": 11, "output_tokens": 3},
    }
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeResponsesClient(payload, seen))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def run():
        chunks = []
        async for chunk in llm_core.stream_llm(
            "https://api.openai.com/v1/chat/completions",
            "gpt-5.5-pro",
            [{"role": "user", "content": "hi"}],
            headers={"Authorization": "Bearer k"},
            max_tokens=25,
            tools=[{"type": "function", "function": {"name": "bash", "parameters": {}}}],
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(run())
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert "messages" not in seen["json"]
    assert seen["json"]["input"] == [{"type": "message", "role": "user", "content": "hi"}]
    assert seen["json"]["max_output_tokens"] == 25
    assert seen["json"]["tools"] == [
        {
            "type": "function",
            "name": "bash",
            "description": "",
            "parameters": {"type": "object", "properties": {}},
            "strict": False,
        }
    ]
    assert any('"delta": "OK from pro"' in c for c in chunks)
    assert any('"input_tokens": 11' in c and '"output_tokens": 3' in c for c in chunks)
    assert chunks[-1] == "data: [DONE]\n\n"


def test_gpt55_pro_streams_responses_sse_events(monkeypatch):
    seen = {}
    lines = [
        _responses_sse({"type": "response.output_text.delta", "delta": "hel"}),
        _responses_sse({"type": "response.output_text.delta", "delta": "lo"}),
        _responses_sse({
            "type": "response.completed",
            "response": {"usage": {"input_tokens": 7, "output_tokens": 2}},
        }),
    ]
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeResponsesStreamClient(lines, seen))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def run():
        events = []
        async for chunk in llm_core.stream_llm(
            "https://api.openai.com/v1/chat/completions",
            "gpt-5.5-pro",
            [{"role": "user", "content": "hi"}],
            headers={"Authorization": "Bearer k"},
        ):
            for ln in chunk.split("\n"):
                if ln.startswith("data: ") and ln[6:] != "[DONE]":
                    events.append(json.loads(ln[6:]))
        return events

    events = asyncio.run(run())
    assert seen["url"] == "https://api.openai.com/v1/responses"
    assert seen["json"]["stream"] is True
    assert [e.get("delta") for e in events if "delta" in e] == ["hel", "lo"]
    assert any(e.get("type") == "usage" and e["data"]["input_tokens"] == 7 for e in events)


def test_responses_bridge_emits_waiting_events_during_slow_call(monkeypatch):
    llm_core._response_cache.clear()
    seen = {}
    payload = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "done"}],
            }
        ]
    }
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _SlowResponsesClient(payload, seen))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "RESPONSES_WAIT_EVENT_INTERVAL", 0.01)

    async def run():
        events = []
        async for chunk in llm_core.stream_llm(
            "https://api.openai.com/v1/chat/completions",
            "gpt-5.5-pro",
            [{"role": "user", "content": "hi"}],
            headers={"Authorization": "Bearer k"},
        ):
            for ln in chunk.split("\n"):
                if ln.startswith("data: ") and ln[6:] != "[DONE]":
                    events.append(json.loads(ln[6:]))
        return events

    events = asyncio.run(run())
    assert events[0]["type"] == "model_waiting"
    assert events[0]["model"] == "gpt-5.5-pro"
    assert any(e.get("delta") == "done" for e in events)


def test_gpt55_still_uses_chat_stream(monkeypatch):
    seen = {}

    class CapturingClient(_FakeClient):
        def stream(self, method, url, **kw):
            seen["url"] = url
            seen["json"] = kw.get("json")
            return super().stream(method, url, **kw)

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: CapturingClient([_sse({"content": "hi"}), "data: [DONE]"]))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def run():
        return [chunk async for chunk in llm_core.stream_llm(
            "https://api.openai.com/v1/chat/completions",
            "gpt-5.5",
            [{"role": "user", "content": "hi"}],
        )]

    asyncio.run(run())
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["json"]["stream"] is True
    assert seen["json"]["messages"] == [{"role": "user", "content": "hi"}]


def test_saved_responses_endpoint_is_not_double_appended(monkeypatch):
    seen = {}
    payload = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeResponsesClient(payload, seen))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def run():
        return [chunk async for chunk in llm_core.stream_llm(
            "https://api.openai.com/v1/responses",
            "gpt-5.5-pro",
            [{"role": "user", "content": "hi"}],
        )]

    asyncio.run(run())
    assert seen["url"] == "https://api.openai.com/v1/responses"


def test_responses_bridge_emits_function_calls(monkeypatch):
    seen = {}
    payload = {
        "output": [
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "web_search",
                "arguments": '{"query":"cats"}',
            }
        ],
    }
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeResponsesClient(payload, seen))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def run():
        events = []
        async for chunk in llm_core.stream_llm(
            "https://api.openai.com/v1/chat/completions",
            "gpt-5.5-pro",
            [{"role": "user", "content": "search"}],
        ):
            for ln in chunk.split("\n"):
                if ln.startswith("data: ") and ln[6:] != "[DONE]":
                    events.append(json.loads(ln[6:]))
        return events

    events = asyncio.run(run())
    calls = next(e["calls"] for e in events if e.get("type") == "tool_calls")
    assert calls == [{"id": "call_1", "name": "web_search", "arguments": '{"query":"cats"}'}]


def test_responses_stream_emits_tool_argument_deltas(monkeypatch):
    seen = {}
    lines = [
        _responses_sse({
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "call_id": "call_1",
                "name": "create_document",
                "arguments": "",
            },
        }),
        _responses_sse({
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": '{"title":"N',
        }),
        _responses_sse({
            "type": "response.function_call_arguments.done",
            "output_index": 0,
            "name": "create_document",
            "call_id": "call_1",
            "arguments": '{"title":"Note"}',
        }),
        _responses_sse({"type": "response.completed", "response": {}}),
    ]
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeResponsesStreamClient(lines, seen))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def run():
        events = []
        async for chunk in llm_core.stream_llm(
            "https://api.openai.com/v1/chat/completions",
            "gpt-5.5-pro",
            [{"role": "user", "content": "write a note"}],
        ):
            for ln in chunk.split("\n"):
                if ln.startswith("data: ") and ln[6:] != "[DONE]":
                    events.append(json.loads(ln[6:]))
        return events

    events = asyncio.run(run())
    assert any(e.get("type") == "tool_call_delta" and e.get("arg_delta") == '{"title":"N' for e in events)
    calls = next(e["calls"] for e in events if e.get("type") == "tool_calls")
    assert calls == [{"id": "call_1", "name": "create_document", "arguments": '{"title":"Note"}'}]


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
