"""ChatGPT Subscription rewrites must resolve fresh request-local auth."""

import json
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import routes.chat_routes as chat_routes


def _json_request(payload):
    body = json.dumps(payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/rewrite",
            "headers": [],
            "query_string": b"",
        },
        receive,
    )


@pytest.mark.asyncio
async def test_chatgpt_rewrite_resolves_auth_before_starting_model_stream(monkeypatch):
    session = SimpleNamespace(
        endpoint_url="https://chatgpt.com/backend-api/codex/responses",
        model="gpt-5.4",
        headers={"Authorization": "Bearer expired"},
        history=[],
    )
    session_manager = SimpleNamespace(
        get_session=lambda session_id: session,
    )
    calls = []

    def refresh_auth(sess, session_id, owner=None):
        calls.append(("refresh_auth", session_id, owner))
        sess.headers = {"Authorization": "Bearer refreshed"}

    async def fake_stream(endpoint_url, model, messages, headers=None, **kwargs):
        calls.append(("stream_llm", headers))
        yield 'data: {"delta": "Shortened response"}\n\n'

    monkeypatch.setattr(chat_routes, "_verify_session_owner", lambda request, session_id: None)
    monkeypatch.setattr(chat_routes, "effective_user", lambda request: "alice")
    monkeypatch.setattr(chat_routes, "resolve_session_auth", refresh_auth)
    monkeypatch.setattr(chat_routes, "stream_llm", fake_stream)

    router = chat_routes.setup_chat_routes(
        session_manager,
        chat_handler=None,
        chat_processor=None,
        memory_manager=None,
        research_handler=None,
        upload_handler=None,
    )
    endpoint = next(
        route.endpoint for route in router.routes if route.path == "/api/rewrite"
    )
    response = await endpoint(
        _json_request({
            "session_id": "session-1",
            "original_text": "A response that should be shorter.",
            "instruction": "Make it shorter.",
        })
    )
    chunks = [chunk async for chunk in response.body_iterator]

    assert calls == [
        ("refresh_auth", "session-1", "alice"),
        ("stream_llm", {"Authorization": "Bearer refreshed"}),
    ]
    assert "Shortened response" in "".join(chunks)
