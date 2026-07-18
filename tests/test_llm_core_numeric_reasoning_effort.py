"""Endpoint-scoped continuous reasoning effort request contract."""

import asyncio

import pytest
from fastapi import HTTPException

from routes import model_routes
from src import llm_core


def test_reasoning_effort_route_validation():
    assert model_routes._normalize_reasoning_effort_type("float") == "float"
    assert model_routes._normalize_reasoning_effort(0) == 0.0
    assert model_routes._normalize_reasoning_effort("0.99") == 0.99


@pytest.mark.parametrize("value", [-0.01, 1, "not-a-number"])
def test_reasoning_effort_route_rejects_invalid_values(value):
    with pytest.raises(HTTPException) as exc_info:
        model_routes._normalize_reasoning_effort(value)
    assert exc_info.value.status_code == 400


def test_numeric_effort_uses_chat_template_kwargs(monkeypatch):
    payload = {"model": "inkling", "reasoning_effort": "high"}
    monkeypatch.setattr(llm_core, "_configured_numeric_reasoning_effort", lambda *_: 0.37)

    llm_core._apply_endpoint_reasoning_effort(payload, "https://model.example/v1", "inkling")

    assert payload["chat_template_kwargs"] == {"reasoning_effort": 0.37}
    assert "reasoning_effort" not in payload


def test_unconfigured_endpoint_does_not_change_payload(monkeypatch):
    payload = {"model": "label-model", "reasoning_effort": "high"}
    monkeypatch.setattr(llm_core, "_configured_numeric_reasoning_effort", lambda *_: None)

    llm_core._apply_endpoint_reasoning_effort(payload, "https://model.example/v1", "label-model")

    assert payload == {"model": "label-model", "reasoning_effort": "high"}


class _Response:
    status_code = 200

    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'
        yield "data: [DONE]"

    async def aread(self):
        return b""


class _Stream:
    async def __aenter__(self):
        return _Response()

    async def __aexit__(self, *_):
        return False


class _Client:
    payload = None

    def stream(self, _method, _url, **kwargs):
        self.payload = kwargs["json"]
        return _Stream()


def test_streaming_tool_round_sends_numeric_effort(monkeypatch):
    client = _Client()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: client)
    monkeypatch.setattr(llm_core, "_configured_numeric_reasoning_effort", lambda *_: 0.99)
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda *_: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *_: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *_: None)

    async def run():
        return [chunk async for chunk in llm_core.stream_llm(
            "https://model.example/v1",
            "inkling",
            [{"role": "user", "content": "inspect"}],
            tools=[{"type": "function", "function": {"name": "grep", "parameters": {}}}],
        )]

    asyncio.run(run())
    assert client.payload["chat_template_kwargs"] == {"reasoning_effort": 0.99}
    assert client.payload["tools"][0]["function"]["name"] == "grep"
