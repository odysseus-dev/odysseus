"""Agent chat history uses the same scope as existing chat and history routes."""

import pytest
from fastapi import APIRouter, HTTPException
from starlette.requests import Request

from routes import codex_routes


def _endpoint(router, method: str, path: str):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _request(scopes: list[str]) -> Request:
    request = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/codex/chat/session-test",
        "headers": [],
        "state": {},
    })
    request.state.current_user = "api"
    request.state.api_token = True
    request.state.api_token_owner = "alice"
    request.state.api_token_scopes = scopes
    return request


def _history_router(calls: list[tuple]):
    router = APIRouter()

    @router.get("/api/history/{session_id}")
    async def history(
        request: Request,
        session_id: str,
        limit: int | None = None,
        offset: int | None = None,
    ):
        calls.append((request, session_id, limit, offset))
        return {
            "name": "Model debugging",
            "model": "org/model",
            "history": [{"role": "assistant", "content": "second"}],
            "total": 3,
            "offset": offset,
            "limit": limit,
            "endpoint_url": "http://private-endpoint",
        }

    return router


@pytest.mark.asyncio
async def test_history_read_uses_existing_paged_handler_without_exposing_endpoint_url():
    calls = []
    router = codex_routes.setup_codex_routes(history_router=_history_router(calls))
    read_chat = _endpoint(router, "GET", "/api/codex/chat/{session_id}")
    request = _request(["chat"])

    result = await read_chat(request, "session-test", offset=1, limit=1)

    assert calls == [(request, "session-test", 1, 1)]
    assert result == {
        "session_id": "session-test",
        "name": "Model debugging",
        "model": "org/model",
        "messages": [{"role": "assistant", "content": "second"}],
        "total": 3,
        "offset": 1,
        "limit": 1,
    }


@pytest.mark.asyncio
async def test_token_without_chat_scope_cannot_read_history_or_reach_handler():
    calls = []
    router = codex_routes.setup_codex_routes(history_router=_history_router(calls))
    read_chat = _endpoint(router, "GET", "/api/codex/chat/{session_id}")

    with pytest.raises(HTTPException) as exc_info:
        await read_chat(_request(["todos:read"]), "session-test")

    assert exc_info.value.status_code == 403
    assert calls == []


@pytest.mark.asyncio
async def test_history_read_uses_existing_page_limit():
    calls = []
    router = codex_routes.setup_codex_routes(history_router=_history_router(calls))
    read_chat = _endpoint(router, "GET", "/api/codex/chat/{session_id}")

    await read_chat(_request(["chat"]), "session-test", limit=1000)

    assert calls[0][2:] == (100, 0)


def test_capabilities_report_history_permission_from_chat_scope():
    router = codex_routes.setup_codex_routes(history_router=_history_router([]))
    capabilities = _endpoint(router, "GET", "/api/codex/capabilities")

    without_chat = capabilities(_request(["todos:read"]))["tools"]["chat"]
    reader = capabilities(_request(["chat"]))["tools"]["chat"]

    assert without_chat["read"] is False
    assert reader["read"] is True
    assert reader["available"] is True
    assert reader["actions"] == ["read"]
