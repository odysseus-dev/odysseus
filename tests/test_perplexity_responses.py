"""Tests for the OpenAI Responses API adapter (Perplexity Agent API).

The Responses API uses `input`/`output` and named SSE events instead of Chat
Completions' `messages`/`choices`. These cover the request builder, the
non-streaming parsers, and the streaming translator that re-emits Odysseus's
internal SSE format — plus a `stream_llm` integration over a canned SSE stream.
"""
import json
import asyncio

import pytest

from src import llm_core
from src.openai_responses import (
    build_responses_payload,
    parse_responses_output,
    parse_responses_usage,
    ResponsesStreamTranslator,
    sanitize_responses_schema,
    responses_tools,
)


# ── request builder ──

class TestBuildResponsesPayload:
    def test_basic_text(self):
        p = build_responses_payload(
            "openai/gpt-5.5",
            [{"role": "user", "content": "hi"}],
            0.7, 100, stream=False, max_steps=1,
        )
        assert p["model"] == "openai/gpt-5.5"
        assert "messages" not in p
        assert p["input"] == [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}
        ]
        assert p["temperature"] == 0.7
        assert p["max_output_tokens"] == 100
        assert p["max_steps"] == 1
        assert "stream" not in p  # stream=False omits the key

    def test_system_becomes_instructions(self):
        p = build_responses_payload(
            "m",
            [{"role": "system", "content": "be brief"},
             {"role": "user", "content": "hi"}],
            1.0, 0, stream=True,
        )
        assert p["instructions"] == "be brief"
        assert all(i.get("role") != "system" for i in p["input"])
        assert p["stream"] is True
        assert "max_output_tokens" not in p  # max_tokens=0
        assert "max_steps" not in p          # not provided

    def test_tools_flattened_strict_false(self):
        tools = [{"type": "function", "function": {
            "name": "get_weather", "description": "w",
            "parameters": {"type": "object", "properties": {"loc": {"type": "string"}}},
        }}]
        p = build_responses_payload("m", [{"role": "user", "content": "x"}], 0.5, 10, stream=True, tools=tools)
        assert p["tools"] == [{
            "type": "function",
            "name": "get_weather",
            "description": "w",
            "parameters": {"type": "object", "properties": {"loc": {"type": "string"}}},
            "strict": False,
        }]

    def test_assistant_tool_calls_round_trip(self):
        msgs = [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "get_weather", "arguments": '{"loc":"SF"}'}},
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "72F"},
        ]
        items = build_responses_payload("m", msgs, 0.5, 10, stream=True)["input"]
        fc = [i for i in items if i.get("type") == "function_call"]
        fco = [i for i in items if i.get("type") == "function_call_output"]
        # call_id must match on both sides or follow-up rounds 400
        assert fc == [{"type": "function_call", "call_id": "call_1",
                       "name": "get_weather", "arguments": '{"loc":"SF"}'}]
        assert fco == [{"type": "function_call_output", "call_id": "call_1", "output": "72F"}]

    def test_multimodal_image_data_uri(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "what's this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}]
        parts = build_responses_payload("m", msgs, 0.5, 10, stream=False)["input"][0]["content"]
        assert parts[0] == {"type": "input_text", "text": "what's this"}
        assert parts[1] == {"type": "input_image",
                            "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}


# ── non-streaming parsers ──

class TestParseResponses:
    def test_output_text_concatenated(self):
        data = {"output": [
            {"type": "reasoning", "summary": [{"type": "summary_text", "text": "thinking"}]},
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "Hello "},
                         {"type": "output_text", "text": "world"}]},
        ]}
        assert parse_responses_output(data) == "Hello world"

    def test_empty_output(self):
        assert parse_responses_output({}) == ""
        assert parse_responses_output({"output": []}) == ""

    def test_usage(self):
        assert parse_responses_usage({"usage": {"input_tokens": 10, "output_tokens": 5}}) == {
            "input_tokens": 10, "output_tokens": 5}

    def test_usage_missing(self):
        assert parse_responses_usage({}) is None


# ── streaming translator ──

def _feed_all(translator, events):
    out = []
    for ev in events:
        out.extend(translator.feed(ev))
    out.extend(translator.flush())
    return out


def _parse_chunks(chunks):
    """Internal SSE chunks → list of parsed JSON dicts (skipping [DONE])."""
    objs = []
    for c in chunks:
        for ln in c.split("\n"):
            ln = ln.strip()
            if ln.startswith("data: ") and ln[6:] != "[DONE]":
                try:
                    objs.append(json.loads(ln[6:]))
                except ValueError:
                    pass
    return objs


class TestResponsesStreamTranslator:
    def test_text_reasoning_usage(self):
        events = [
            {"type": "response.created"},
            {"type": "response.reasoning_summary_text.delta", "delta": "think "},
            {"type": "response.output_text.delta", "delta": "Hello "},
            {"type": "response.output_text.delta", "delta": "world"},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 3, "output_tokens": 2}}},
        ]
        chunks = _feed_all(ResponsesStreamTranslator(), events)
        assert chunks[-1] == "data: [DONE]\n\n"
        objs = _parse_chunks(chunks)
        assert {"delta": "think ", "thinking": True} in objs
        assert {"delta": "Hello "} in objs
        assert {"delta": "world"} in objs
        assert {"type": "usage", "data": {"input_tokens": 3, "output_tokens": 2}} in objs

    def test_function_call_streaming(self):
        events = [
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "function_call", "id": "fc_1", "call_id": "call_x", "name": "search", "arguments": ""}},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"q":'},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '"cats"}'},
            {"type": "response.function_call_arguments.done", "item_id": "fc_1", "arguments": '{"q":"cats"}'},
            {"type": "response.output_item.done", "output_index": 0,
             "item": {"type": "function_call", "id": "fc_1", "call_id": "call_x",
                      "name": "search", "arguments": '{"q":"cats"}'}},
            {"type": "response.completed", "response": {"usage": {"input_tokens": 1, "output_tokens": 1}}},
        ]
        chunks = _feed_all(ResponsesStreamTranslator(), events)
        tc = next(o for o in _parse_chunks(chunks) if o.get("type") == "tool_calls")
        # id = call_id (round-trips to function_call.call_id next round)
        assert tc["calls"] == [{"id": "call_x", "name": "search", "arguments": '{"q":"cats"}'}]
        assert chunks[-1] == "data: [DONE]\n\n"

    def test_parallel_function_calls(self):
        events = [
            {"type": "response.output_item.added", "output_index": 0,
             "item": {"type": "function_call", "id": "fc_1", "call_id": "call_a", "name": "f0"}},
            {"type": "response.output_item.added", "output_index": 1,
             "item": {"type": "function_call", "id": "fc_2", "call_id": "call_b", "name": "f1"}},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": "{}"},
            {"type": "response.function_call_arguments.delta", "item_id": "fc_2", "delta": "{}"},
            {"type": "response.completed", "response": {}},
        ]
        tc = next(o for o in _parse_chunks(_feed_all(ResponsesStreamTranslator(), events))
                  if o.get("type") == "tool_calls")
        by_id = {c["id"]: c for c in tc["calls"]}
        assert set(by_id) == {"call_a", "call_b"}
        assert by_id["call_a"]["name"] == "f0"
        assert by_id["call_b"]["name"] == "f1"

    def test_failed_emits_error_no_done(self):
        t = ResponsesStreamTranslator()
        chunks = []
        for ev in [
            {"type": "response.output_text.delta", "delta": "partial"},
            {"type": "response.failed", "response": {"error": {"message": "model_error"}}},
        ]:
            chunks.extend(t.feed(ev))
        chunks.extend(t.flush())   # must NOT append a [DONE] after a failure
        joined = "".join(chunks)
        assert "event: error" in joined
        assert "model_error" in joined
        assert "[DONE]" not in joined

    def test_reasoning_block_without_deltas(self):
        events = [
            {"type": "response.output_item.done", "output_index": 0,
             "item": {"type": "reasoning", "summary": [{"type": "summary_text", "text": "the plan"}]}},
            {"type": "response.completed", "response": {}},
        ]
        objs = _parse_chunks(_feed_all(ResponsesStreamTranslator(), events))
        assert {"delta": "the plan", "thinking": True} in objs

    def test_calls_fallback_from_completed_output(self):
        # No streamed arg deltas — calls live only in completed.output.
        events = [
            {"type": "response.completed", "response": {
                "output": [{"type": "function_call", "call_id": "call_z", "name": "g", "arguments": "{}"}],
                "usage": {"input_tokens": 1, "output_tokens": 0},
            }},
        ]
        tc = next(o for o in _parse_chunks(_feed_all(ResponsesStreamTranslator(), events))
                  if o.get("type") == "tool_calls")
        assert tc["calls"] == [{"id": "call_z", "name": "g", "arguments": "{}"}]

    def test_error_frame_surfaces_without_top_level_type(self):
        # Perplexity's `error` SSE frame: {"error":{...}}, no top-level "type".
        # Must surface as an error (not a silent empty reply) with no [DONE].
        out = ResponsesStreamTranslator().feed(
            {"error": {"message": "invalid request", "type": "invalid_request", "code": 400}})
        joined = "".join(out)
        assert joined.startswith("event: error")
        assert "invalid request" in joined
        assert json.loads(joined.split("data: ", 1)[1])["status"] == 400
        assert "[DONE]" not in joined


# ── tool-schema sanitization (Perplexity validator is stricter than OpenAI) ──

class TestSchemaSanitizer:
    def test_object_without_properties_gets_empty_props(self):
        assert sanitize_responses_schema({"type": "object"})["properties"] == {}

    def test_nested_freeform_object_fixed(self):
        s = sanitize_responses_schema(
            {"type": "object", "properties": {"body": {"type": "object", "description": "json"}}})
        assert s["properties"]["body"]["properties"] == {}

    def test_object_inside_array_items_fixed(self):
        s = sanitize_responses_schema({"type": "array", "items": {"type": "object"}})
        assert s["items"]["properties"] == {}

    def test_typeless_property_gets_string_type(self):
        s = sanitize_responses_schema(
            {"type": "object", "properties": {"value": {"description": "any value"}}})
        assert s["properties"]["value"]["type"] == "string"

    def test_composition_node_not_forced_to_string(self):
        node = sanitize_responses_schema({"anyOf": [{"type": "string"}, {"type": "number"}]})
        assert "type" not in node  # anyOf preserved

    def test_properties_without_type_becomes_object(self):
        node = sanitize_responses_schema({"properties": {"a": {"type": "string"}}})
        assert node["type"] == "object"

    def test_responses_tools_flattens_and_sanitizes(self):
        tools = [{"type": "function", "function": {
            "name": "f", "description": "d",
            "parameters": {"type": "object", "properties": {
                "body": {"type": "object"}, "v": {"description": "x"}}}}}]
        out = responses_tools(tools)
        assert out[0]["name"] == "f" and out[0]["strict"] is False
        props = out[0]["parameters"]["properties"]
        assert props["body"]["properties"] == {} and props["v"]["type"] == "string"

    def test_does_not_mutate_caller_schema(self):
        original = {"type": "object", "properties": {"body": {"type": "object"}}}
        responses_tools([{"type": "function", "function": {"name": "f", "parameters": original}}])
        assert "properties" not in original["properties"]["body"]  # deep-copied


# ── stream_llm integration over a canned Responses SSE stream ──

class _FakeResp:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status

    async def aiter_lines(self):
        for ln in self._lines:
            yield ln

    async def aread(self):
        return b""


class _FakeStreamCtx:
    def __init__(self, lines, status):
        self._lines, self._status = lines, status

    async def __aenter__(self):
        return _FakeResp(self._lines, self._status)

    async def __aexit__(self, *a):
        return False


class _FakeClient:
    def __init__(self, lines, status=200):
        self._lines, self._status = lines, status

    def stream(self, method, url, **kw):
        return _FakeStreamCtx(self._lines, self._status)


def _drive(monkeypatch, lines, tools=None, status=200):
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeClient(lines, status))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda u: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **k: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a, **k: None)

    async def run():
        chunks = []
        async for chunk in llm_core.stream_llm(
            "https://api.perplexity.ai/v1/responses",
            "openai/gpt-5.5",
            [{"role": "user", "content": "hi"}],
            headers={"Authorization": "Bearer k"},
            tools=tools,
        ):
            chunks.append(chunk)
        return chunks

    return asyncio.run(run())


def _evt(etype, **fields):
    return "data: " + json.dumps({"type": etype, **fields})


def test_stream_llm_perplexity_text(monkeypatch):
    lines = [
        _evt("response.output_text.delta", delta="Hello"),
        _evt("response.output_text.delta", delta=" world"),
        _evt("response.completed", response={"usage": {"input_tokens": 2, "output_tokens": 2}}),
    ]
    chunks = _drive(monkeypatch, lines)
    objs = _parse_chunks(chunks)
    text = "".join(o["delta"] for o in objs if "delta" in o and not o.get("thinking"))
    assert text == "Hello world"
    assert {"type": "usage", "data": {"input_tokens": 2, "output_tokens": 2}} in objs
    assert any("[DONE]" in c for c in chunks)


def test_stream_llm_perplexity_tool_call(monkeypatch):
    lines = [
        _evt("response.output_item.added", output_index=0,
             item={"type": "function_call", "id": "fc_1", "call_id": "call_x", "name": "search"}),
        _evt("response.function_call_arguments.delta", item_id="fc_1", delta='{"q":"x"}'),
        _evt("response.output_item.done", output_index=0,
             item={"type": "function_call", "id": "fc_1", "call_id": "call_x",
                   "name": "search", "arguments": '{"q":"x"}'}),
        _evt("response.completed", response={"usage": {"input_tokens": 1, "output_tokens": 1}}),
    ]
    chunks = _drive(monkeypatch, lines,
                    tools=[{"type": "function", "function": {"name": "search", "parameters": {}}}])
    tc = next(o for o in _parse_chunks(chunks) if o.get("type") == "tool_calls")
    assert tc["calls"] == [{"id": "call_x", "name": "search", "arguments": '{"q":"x"}'}]


def test_stream_llm_perplexity_http_error(monkeypatch):
    chunks = _drive(monkeypatch, ["irrelevant"], status=401)
    joined = "".join(chunks)
    assert "event: error" in joined
    assert "[DONE]" not in joined
