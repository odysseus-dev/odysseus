import json
from contextlib import asynccontextmanager

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from routes import codex_mcp


class _FakeManager:
    def __init__(self):
        self.calls = []
        self.tools = [
            {
                "server_id": "external",
                "server_name": "Test server",
                "name": "echo",
                "qualified_name": "mcp__external__echo",
                "description": "Echo text",
                "input_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                "is_disabled": False,
            },
            {
                "server_id": "external",
                "server_name": "Test server",
                "name": "hidden",
                "qualified_name": "mcp__external__hidden",
                "description": "Hidden tool",
                "input_schema": {"type": "object"},
                "is_disabled": True,
            },
            {
                "server_id": "rag",
                "server_name": "Built-in RAG",
                "name": "search",
                "qualified_name": "mcp__rag__search",
                "description": "Built-in tool",
                "input_schema": {"type": "object"},
                "is_disabled": False,
            },
        ]

    def get_all_tools(self, disabled_map):
        tools = []
        for tool in self.tools:
            item = dict(tool)
            item["is_disabled"] = item["name"] in disabled_map.get(item["server_id"], set())
            tools.append(item)
        return tools

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return {"stdout": arguments["text"], "stderr": "", "exit_code": 0}


class _TestAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"").decode()
        if not authorization:
            await JSONResponse({"error": "Not authenticated"}, status_code=401)(scope, receive, send)
            return
        if not authorization.startswith("Bearer valid-"):
            await JSONResponse({"error": "Invalid API token"}, status_code=401)(scope, receive, send)
            return
        scope.setdefault("state", {})["api_token"] = True
        scope["state"]["api_token_scopes"] = authorization.removeprefix("Bearer valid-").split(",")
        await self.app(scope, receive, send)


def _app(bridge):
    app = Starlette()
    app.mount("/api/codex/mcp", bridge)
    return _TestAuth(app)


@asynccontextmanager
async def _streams(app, scopes):
    async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"authorization": f"Bearer valid-{scopes}"},
    ) as client:
        async with streamable_http_client(
            "http://test/api/codex/mcp/",
            http_client=client,
        ) as streams:
            yield streams


@pytest.mark.asyncio
async def test_bridge_requires_valid_bearer_token(monkeypatch):
    monkeypatch.setattr(codex_mcp, "_load_server_policy", lambda: ({"external"}, {}))
    bridge = codex_mcp.CodexMcpBridge(_FakeManager())
    app = _app(bridge)
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    headers = {"accept": "application/json, text/event-stream"}

    async with bridge.run(), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.post("/api/codex/mcp/", json=request, headers=headers)
        invalid = await client.post(
            "/api/codex/mcp/",
            json=request,
            headers={**headers, "authorization": "Bearer invalid"},
        )
        unscoped = await client.post(
            "/api/codex/mcp/",
            json=request,
            headers={**headers, "authorization": "Bearer valid-chat"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert unscoped.status_code == 403


@pytest.mark.asyncio
async def test_bridge_lists_calls_and_reflects_live_policy(monkeypatch):
    policy = {"enabled": {"external"}, "disabled": {"external": {"hidden"}}}
    monkeypatch.setattr(
        codex_mcp,
        "_load_server_policy",
        lambda: (set(policy["enabled"]), dict(policy["disabled"])),
    )
    manager = _FakeManager()
    bridge = codex_mcp.CodexMcpBridge(manager)
    app = _app(bridge)

    async with bridge.run():
        async with _streams(app, "mcp:read,mcp:call") as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert [tool.name for tool in listed.tools] == ["mcp__external__echo"]
                assert listed.tools[0].description == "[MCP:Test server] Echo text"
                assert listed.tools[0].inputSchema["required"] == ["text"]

                called = await session.call_tool("mcp__external__echo", {"text": "round trip"})
                assert called.isError is False
                assert called.content[0].text == "round trip"
                assert manager.calls == [("mcp__external__echo", {"text": "round trip"})]

                policy["enabled"].clear()
                assert (await session.list_tools()).tools == []
                unavailable = await session.call_tool("mcp__external__echo", {"text": "blocked"})
                assert unavailable.isError is True
                assert manager.calls == [("mcp__external__echo", {"text": "round trip"})]


@pytest.mark.asyncio
async def test_read_scope_cannot_call_tools(monkeypatch):
    monkeypatch.setattr(
        codex_mcp,
        "_load_server_policy",
        lambda: ({"external"}, {"external": {"hidden"}}),
    )
    manager = _FakeManager()
    bridge = codex_mcp.CodexMcpBridge(manager)
    app = _app(bridge)

    async with bridge.run():
        async with _streams(app, "mcp:read") as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 1
                result = await session.call_tool("mcp__external__echo", {"text": "no"})

    assert result.isError is True
    assert "mcp:call" in result.content[0].text
    assert manager.calls == []


def test_call_result_preserves_text_and_images():
    result = codex_mcp._call_result({
        "stdout": "done",
        "exit_code": 0,
        "images": [{"data": "aGVsbG8=", "mimeType": "image/png"}],
    })
    assert result.isError is False
    assert result.content[0].text == "done"
    assert result.content[1].data == "aGVsbG8="


def test_server_policy_ignores_malformed_disabled_tools(monkeypatch):
    rows = [
        type("Row", (), {"id": "on", "is_enabled": True, "disabled_tools": '["x"]'})(),
        type("Row", (), {"id": "off", "is_enabled": False, "disabled_tools": None})(),
        type("Row", (), {"id": "bad", "is_enabled": True, "disabled_tools": "{"})(),
    ]

    class Session:
        def query(self, _model):
            return self

        def all(self):
            return rows

        def close(self):
            pass

    monkeypatch.setattr(codex_mcp, "SessionLocal", Session)
    assert codex_mcp._load_server_policy() == ({"on", "bad"}, {"on": {"x"}})
