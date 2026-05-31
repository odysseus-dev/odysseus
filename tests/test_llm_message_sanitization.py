"""Regression tests for provider-boundary chat message sanitization."""

import pytest

from src import llm_core


MESSAGES_WITH_INTERNAL_FIELDS = [
    {
        "role": "user",
        "content": "hello",
        "metadata": {"source": "ui", "_db_id": "123"},
        "_protected": True,
    },
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call-1", "type": "function"}],
        "metadata": {"model": "test-model"},
    },
    {
        "role": "tool",
        "content": "done",
        "tool_call_id": "call-1",
    },
]


def _assert_sanitized(messages):
    assert messages == [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "type": "function"}],
        },
        {"role": "tool", "content": "done", "tool_call_id": "call-1"},
    ]


def test_sanitize_messages_removes_internal_fields_without_mutating_input():
    messages = [dict(message) for message in MESSAGES_WITH_INTERNAL_FIELDS]

    sanitized = llm_core._sanitize_messages_for_api(messages)

    _assert_sanitized(sanitized)
    assert "metadata" in messages[0]
    assert "_protected" in messages[0]


def test_llm_call_sanitizes_messages_before_post(monkeypatch):
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, *, headers, json, timeout):
        captured["messages"] = json["messages"]
        return Response()

    monkeypatch.setattr(llm_core.httpx, "post", fake_post)

    llm_core.llm_call(
        "http://sync-provider.test/v1/chat/completions",
        "test-model",
        MESSAGES_WITH_INTERNAL_FIELDS,
    )

    _assert_sanitized(captured["messages"])


@pytest.mark.asyncio
async def test_llm_call_async_sanitizes_messages_before_post(monkeypatch):
    captured = {}

    class Response:
        is_success = True

        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"}}]}

    class Client:
        async def post(self, url, *, headers, json, timeout):
            captured["messages"] = json["messages"]
            return Response()

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: Client())

    await llm_core.llm_call_async(
        "http://async-provider.test/v1/chat/completions",
        "test-model",
        MESSAGES_WITH_INTERNAL_FIELDS,
    )

    _assert_sanitized(captured["messages"])


@pytest.mark.asyncio
async def test_stream_llm_sanitizes_messages_before_post(monkeypatch):
    captured = {}

    class Response:
        status_code = 200

        async def aiter_lines(self):
            yield 'data: {"choices": [{"delta": {"content": "ok"}}]}'
            yield "data: [DONE]"

    class StreamContext:
        async def __aenter__(self):
            return Response()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class Client:
        def stream(self, method, url, *, json, headers, timeout):
            captured["messages"] = json["messages"]
            return StreamContext()

    monkeypatch.setattr(llm_core, "_get_http_client", lambda: Client())

    chunks = [
        chunk
        async for chunk in llm_core.stream_llm(
            "http://stream-provider.test/v1/chat/completions",
            "test-model",
            MESSAGES_WITH_INTERNAL_FIELDS,
        )
    ]

    _assert_sanitized(captured["messages"])
    assert chunks[-1] == "data: [DONE]\n\n"
