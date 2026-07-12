import asyncio
import json

from src.chatgpt_subscription import (
    ResponsesToolCallAccumulator,
    build_responses_input,
    build_responses_tools,
)
from src import llm_core
from src.llm_core import _build_chatgpt_responses_payload


def _tool_schema(name="get_workspace"):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "A test tool",
            "parameters": {"type": "object", "properties": {}},
            "strict": True,
        },
    }


def _reasoning_item():
    return {
        "id": "rs_1",
        "type": "reasoning",
        "summary": [{"type": "summary_text", "text": "opaque summary"}],
        "encrypted_content": "encrypted-reasoning",
    }


def test_responses_tool_schema_is_flattened():
    assert build_responses_tools([_tool_schema()]) == [{
        "type": "function",
        "name": "get_workspace",
        "description": "A test tool",
        "parameters": {"type": "object", "properties": {}},
        "strict": True,
    }]


def test_tool_history_preserves_call_id_and_string_output():
    messages = [
        {"role": "user", "content": "Call it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_123",
                "type": "function",
                "function": {"name": "get_workspace", "arguments": "{}"},
            }],
        },
        {"role": "tool", "tool_call_id": "call_123", "content": {"path": "C:/odysseus"}},
    ]
    items = build_responses_input(messages, model="gpt-5.6-sol")
    assert items[1] == {
        "type": "function_call",
        "call_id": "call_123",
        "name": "get_workspace",
        "arguments": "{}",
    }
    assert items[2]["type"] == "function_call_output"
    assert items[2]["call_id"] == "call_123"
    assert isinstance(items[2]["output"], str)
    assert json.loads(items[2]["output"]) == {"path": "C:/odysseus"}


def test_reasoning_is_replayed_before_function_call_for_same_model():
    messages = [{
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_workspace", "arguments": "{}"},
            "extra_content": {
                "responses_model": "gpt-5.6-sol-2026-07-01",
                "responses_reasoning_items": [_reasoning_item()],
            },
        }],
    }]
    items = build_responses_input(messages, model="gpt-5.6-sol")
    assert items[0] == _reasoning_item()
    assert items[1]["type"] == "function_call"


def test_reasoning_is_not_replayed_across_incompatible_models():
    messages = [{
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "get_workspace", "arguments": "{}"},
            "extra_content": {
                "responses_model": "gpt-5.6-sol",
                "responses_reasoning_items": [_reasoning_item()],
            },
        }],
    }]
    items = build_responses_input(messages, model="gpt-5.6-terra")
    assert [item["type"] for item in items] == ["function_call"]


def test_payload_contains_responses_tools_and_reasoning_replay_request():
    payload = _build_chatgpt_responses_payload(
        "gpt-5.6-sol",
        [{"role": "user", "content": "test"}],
        0.2,
        0,
        stream=True,
        tools=[_tool_schema()],
    )
    assert payload["tools"][0]["name"] == "get_workspace"
    assert "function" not in payload["tools"][0]
    assert payload["tool_choice"] == "auto"
    assert payload["parallel_tool_calls"] is True
    assert payload["include"] == ["reasoning.encrypted_content"]


def test_payload_honors_tool_choice_none():
    payload = _build_chatgpt_responses_payload(
        "gpt-5.6-sol",
        [{"role": "user", "content": "test"}],
        0.2,
        0,
        stream=True,
        tools=[_tool_schema()],
        tool_choice_none=True,
    )
    assert payload["tool_choice"] == "none"


def test_stream_accumulator_uses_final_arguments_and_replay_metadata():
    acc = ResponsesToolCallAccumulator()
    acc.feed({
        "type": "response.output_item.added",
        "output_index": 0,
        "item": _reasoning_item(),
    })
    acc.feed({
        "type": "response.output_item.added",
        "output_index": 1,
        "item": {
            "id": "fc_1",
            "type": "function_call",
            "call_id": "call_1",
            "name": "read_file",
            "arguments": "",
        },
    })
    acc.feed({
        "type": "response.function_call_arguments.delta",
        "output_index": 1,
        "item_id": "fc_1",
        "delta": '{"path":',
    })
    acc.feed({
        "type": "response.function_call_arguments.delta",
        "output_index": 1,
        "item_id": "fc_1",
        "delta": '"README.md"}',
    })
    acc.feed({
        "type": "response.completed",
        "response": {
            "model": "gpt-5.6-sol",
            "output": [
                _reasoning_item(),
                {
                    "id": "fc_1",
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "read_file",
                    "arguments": '{"path":"README.md"}',
                },
            ],
        },
    })
    calls = acc.calls()
    assert len(calls) == 1
    assert calls[0]["id"] == "call_1"
    assert calls[0]["arguments"] == '{"path":"README.md"}'
    assert calls[0]["extra_content"]["responses_model"] == "gpt-5.6-sol"
    assert calls[0]["extra_content"]["responses_reasoning_items"] == [_reasoning_item()]


def test_parallel_calls_keep_order_and_ids():
    acc = ResponsesToolCallAccumulator()
    for index, (call_id, name) in enumerate((("call_a", "get_workspace"), ("call_b", "read_file"))):
        acc.feed({
            "type": "response.output_item.done",
            "output_index": index,
            "item": {
                "id": f"fc_{index}",
                "type": "function_call",
                "call_id": call_id,
                "name": name,
                "arguments": "{}",
            },
        })
    assert [call["id"] for call in acc.calls()] == ["call_a", "call_b"]


def test_actual_chatgpt_stream_parser_emits_normalized_tool_calls(monkeypatch):
    reasoning = _reasoning_item()
    function_call = {
        "id": "fc_1",
        "type": "function_call",
        "call_id": "call_1",
        "name": "get_workspace",
        "arguments": "{}",
    }
    lines = [
        "data: " + json.dumps({
            "type": "response.output_item.added",
            "output_index": 0,
            "item": reasoning,
        }),
        "data: " + json.dumps({
            "type": "response.output_item.added",
            "output_index": 1,
            "item": function_call,
        }),
        "data: " + json.dumps({
            "type": "response.completed",
            "response": {
                "model": "gpt-5.6-sol",
                "output": [reasoning, function_call],
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        }),
    ]

    class FakeResponse:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            for line in lines:
                yield line

    class FakeClient:
        def __init__(self):
            self.payload = None

        def stream(self, method, url, **kwargs):
            self.payload = kwargs.get("json")
            return FakeResponse()

    fake_client = FakeClient()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: fake_client)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda _url: False)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda _url: None)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *_args, **_kwargs: None)

    async def collect():
        return [
            chunk
            async for chunk in llm_core._stream_llm_inner(
                "https://chatgpt.com/backend-api/codex",
                "gpt-5.6-sol",
                [{"role": "user", "content": "Call get_workspace"}],
                temperature=0.2,
                tools=[_tool_schema()],
                headers={"Authorization": "Bearer test"},
            )
        ]

    chunks = asyncio.run(collect())
    assert fake_client.payload["tools"][0]["name"] == "get_workspace"
    decoded = []
    for chunk in chunks:
        if not chunk.startswith("data: {"):
            continue
        decoded.append(json.loads(chunk[len("data: "):].strip()))
    tool_events = [item for item in decoded if item.get("type") == "tool_calls"]
    assert len(tool_events) == 1
    assert tool_events[0]["calls"][0]["id"] == "call_1"
    assert tool_events[0]["calls"][0]["name"] == "get_workspace"
    assert tool_events[0]["calls"][0]["extra_content"]["responses_reasoning_items"] == [reasoning]
