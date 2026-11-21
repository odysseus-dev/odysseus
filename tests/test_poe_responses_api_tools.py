"""Regression tests for Poe OpenAI Responses API (/v1/responses) tool calling (#6088)."""

import json
import pytest

from src import llm_core


class _FakeStreamResponse:
    def __init__(self, lines, status_code=200):
        self._lines = lines
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aread(self):
        return b'{"error": {"message": "upstream error"}}'

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeHttpClient:
    def __init__(self, lines, captured):
        self._lines = lines
        self._captured = captured

    def stream(self, method, url, *, json=None, headers=None, timeout=None):
        self._captured["method"] = method
        self._captured["url"] = url
        self._captured["json"] = json
        self._captured["headers"] = headers
        return _FakeStreamResponse(self._lines)


def test_poe_provider_detection_and_url_normalization():
    assert llm_core._detect_provider("https://api.poe.com/v1") == "poe"
    assert llm_core._provider_label("https://api.poe.com/v1") == "Poe"
    assert llm_core._stream_target_url("https://api.poe.com/v1") == "https://api.poe.com/v1/responses"
    assert llm_core._normalize_openai_responses_url("https://api.poe.com") == "https://api.poe.com/v1/responses"
    assert llm_core._normalize_openai_responses_url("https://api.poe.com/v1/chat/completions") == "https://api.poe.com/v1/responses"
    assert llm_core._normalize_openai_responses_url("https://api.poe.com/v1/responses") == "https://api.poe.com/v1/responses"
    assert llm_core._model_list_base("https://api.poe.com/v1/responses") == "https://api.poe.com/v1"


def test_poe_responses_payload_serializes_tools_and_multi_turn_tool_outputs():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "edit_document",
                "description": "Edit a document by finding and replacing text",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "find": {"type": "string"},
                        "replace": {"type": "string"},
                    },
                    "required": ["find", "replace"],
                },
            },
        }
    ]
    messages = [
        {"role": "system", "content": "You are a document assistant."},
        {"role": "user", "content": "Translate line 1 to Spanish."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "toolu_01FVgyZJknF2FWzoW24LPVPH",
                    "type": "function",
                    "function": {
                        "name": "edit_document",
                        "arguments": '{"find": "hello", "replace": "hola"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "toolu_01FVgyZJknF2FWzoW24LPVPH",
            "content": '{"ok": true, "version": 2}',
        },
    ]

    payload = llm_core._build_openai_responses_payload(
        "claude-sonnet-4.6",
        messages,
        0.7,
        2048,
        stream=True,
        tools=tools,
        provider="poe",
    )

    assert payload["model"] == "claude-sonnet-4.6"
    assert payload["instructions"] == "You are a document assistant."
    assert payload["stream"] is True
    assert payload["max_output_tokens"] == 2048
    assert payload["tool_choice"] == "auto"
    assert payload["tools"] == [
        {
            "type": "function",
            "name": "edit_document",
            "description": "Edit a document by finding and replacing text",
            "parameters": {
                "type": "object",
                "properties": {
                    "find": {"type": "string"},
                    "replace": {"type": "string"},
                },
                "required": ["find", "replace"],
            },
        }
    ]
    assert payload["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "Translate line 1 to Spanish."}]},
        {
            "type": "function_call",
            "call_id": "toolu_01FVgyZJknF2FWzoW24LPVPH",
            "name": "edit_document",
            "arguments": '{"find": "hello", "replace": "hola"}',
        },
        {
            "type": "function_call_output",
            "call_id": "toolu_01FVgyZJknF2FWzoW24LPVPH",
            "output": '{"ok": true, "version": 2}',
        },
    ]


@pytest.mark.asyncio
async def test_stream_llm_poe_emits_tool_calls_from_responses_sse(monkeypatch):
    captured = {}
    sse_lines = [
        'data: {"type": "response.output_item.added", "output_index": 0, "item": {"id": "fc_1", "type": "function_call", "call_id": "toolu_01FVgyZJknF2FWzoW24LPVPH", "name": "edit_document", "arguments": ""}}',
        'data: {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "output_index": 0, "delta": "{\\"find\\": \\"hello\\","}',
        'data: {"type": "response.function_call_arguments.delta", "item_id": "fc_1", "output_index": 0, "delta": " \\"replace\\": \\"world\\"}"}',
        'data: {"type": "response.completed", "response": {"model": "claude-sonnet-4.6", "usage": {"input_tokens": 120, "output_tokens": 24}}}',
    ]
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeHttpClient(sse_lines, captured))

    tools = [
        {
            "type": "function",
            "function": {
                "name": "edit_document",
                "description": "Edit document",
                "parameters": {"type": "object", "properties": {"find": {"type": "string"}, "replace": {"type": "string"}}},
            },
        }
    ]
    chunks = []
    async for chunk in llm_core.stream_llm(
        "https://api.poe.com/v1",
        "claude-sonnet-4.6",
        [{"role": "user", "content": "Replace hello with world"}],
        headers={"Authorization": "Bearer poe-test-key"},
        tools=tools,
    ):
        chunks.append(chunk)

    assert captured["url"] == "https://api.poe.com/v1/responses"
    assert captured["json"]["tools"][0]["name"] == "edit_document"

    parsed_events = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                parsed_events.append(json.loads(line[5:].strip()))

    tool_events = [evt for evt in parsed_events if evt.get("type") == "tool_calls"]
    assert len(tool_events) == 1
    assert tool_events[0]["calls"] == [
        {
            "id": "toolu_01FVgyZJknF2FWzoW24LPVPH",
            "name": "edit_document",
            "arguments": '{"find": "hello", "replace": "world"}',
        }
    ]


@pytest.mark.asyncio
async def test_stream_llm_poe_emits_tool_calls_from_completed_output_items(monkeypatch):
    captured = {}
    sse_lines = [
        'data: {"type": "response.completed", "response": {"model": "claude-sonnet-4.6", "output": [{"type": "function_call", "call_id": "toolu_completed_1", "name": "edit_document", "arguments": "{\\"find\\": \\"a\\", \\"replace\\": \\"b\\"}"}]}}',
    ]
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeHttpClient(sse_lines, captured))

    chunks = []
    async for chunk in llm_core.stream_llm(
        "https://api.poe.com/v1",
        "claude-sonnet-4.6",
        [{"role": "user", "content": "Edit doc"}],
        tools=[{"type": "function", "function": {"name": "edit_document", "parameters": {"type": "object"}}}],
    ):
        chunks.append(chunk)

    tool_events = [
        json.loads(line[5:].strip())
        for chunk in chunks
        for line in chunk.splitlines()
        if line.startswith("data:") and line[5:].strip() != "[DONE]"
    ]
    tool_events = [e for e in tool_events if e.get("type") == "tool_calls"]
    assert len(tool_events) == 1
    assert tool_events[0]["calls"] == [
        {
            "id": "toolu_completed_1",
            "name": "edit_document",
            "arguments": '{"find": "a", "replace": "b"}',
        }
    ]


@pytest.mark.asyncio
async def test_llm_call_poe_collects_responses_text_deltas(monkeypatch):
    captured = {}
    sse_lines = [
        'data: {"type": "response.output_text.delta", "delta": "Document "}',
        'data: {"type": "response.output_text.delta", "delta": "Summary"}',
        'data: {"type": "response.completed", "response": {"model": "claude-sonnet-4.6"}}',
    ]
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _FakeHttpClient(sse_lines, captured))

    result = await llm_core.llm_call_async(
        "https://api.poe.com/v1",
        "claude-sonnet-4.6",
        [{"role": "user", "content": "Summarize"}],
    )
    assert captured["url"] == "https://api.poe.com/v1/responses"
    assert result == "Document Summary"


