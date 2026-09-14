"""Authenticated Streamable HTTP bridge for Codex-native MCP tools."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from mcp import types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse

from core.database import McpServer, SessionLocal

if TYPE_CHECKING:
    from src.mcp_manager import McpManager


MCP_READ_SCOPE = "mcp:read"
MCP_CALL_SCOPE = "mcp:call"
_request_scopes: ContextVar[frozenset[str]] = ContextVar(
    "codex_mcp_request_scopes",
    default=frozenset(),
)


def _load_server_policy() -> tuple[set[str], dict[str, set[str]]]:
    """Return enabled server IDs and their disabled tool names."""
    db = SessionLocal()
    try:
        enabled: set[str] = set()
        disabled: dict[str, set[str]] = {}
        for server in db.query(McpServer).all():
            if not server.is_enabled:
                continue
            enabled.add(server.id)
            if not server.disabled_tools:
                continue
            try:
                names = json.loads(server.disabled_tools)
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(names, list):
                disabled[server.id] = {
                    str(name) for name in names if isinstance(name, str) and name
                }
        return enabled, disabled
    finally:
        db.close()


async def _available_tools(mcp_manager: McpManager) -> dict[str, dict[str, Any]]:
    enabled, disabled = await asyncio.to_thread(_load_server_policy)
    return {
        tool["qualified_name"]: tool
        for tool in mcp_manager.get_all_tools(disabled)
        if tool["server_id"] in enabled and not tool["is_disabled"]
    }


def _tool_description(tool: dict[str, Any]) -> str:
    description = str(tool.get("description") or "").strip()
    label = str(tool.get("server_name") or tool["server_id"])
    return f"[MCP:{label}] {description}".rstrip()


def _call_result(result: dict[str, Any]) -> types.CallToolResult:
    is_error = bool(result.get("exit_code")) or bool(result.get("error"))
    text = str(
        result.get("stderr")
        or result.get("error")
        or result.get("stdout")
        or ""
    )
    content: list[types.TextContent | types.ImageContent] = []
    if text:
        content.append(types.TextContent(type="text", text=text))
    for image in result.get("images") or []:
        if not isinstance(image, dict) or not image.get("data"):
            continue
        content.append(types.ImageContent(
            type="image",
            data=str(image["data"]),
            mimeType=str(image.get("mimeType") or "image/png"),
        ))
    if is_error and not content:
        content.append(types.TextContent(type="text", text="MCP tool call failed"))
    return types.CallToolResult(content=content, isError=is_error)


class CodexMcpBridge:
    """ASGI bridge with a restart-safe transport lifecycle."""

    def __init__(self, mcp_manager: McpManager):
        self.mcp_manager = mcp_manager
        self.server = Server("odysseus", version="1")
        self._session_manager: StreamableHTTPSessionManager | None = None

        @self.server.list_tools()
        async def list_tools() -> list[types.Tool]:
            tools = await _available_tools(self.mcp_manager)
            return [
                types.Tool(
                    name=name,
                    description=_tool_description(tool),
                    inputSchema=(
                        tool.get("input_schema")
                        if isinstance(tool.get("input_schema"), dict)
                        else {"type": "object", "properties": {}}
                    ),
                )
                for name, tool in tools.items()
            ]

        @self.server.call_tool(validate_input=True)
        async def call_tool(name: str, arguments: dict[str, Any]):
            if MCP_CALL_SCOPE not in _request_scopes.get():
                return types.CallToolResult(
                    content=[types.TextContent(
                        type="text",
                        text=f"API token missing required scope: {MCP_CALL_SCOPE}",
                    )],
                    isError=True,
                )
            tools = await _available_tools(self.mcp_manager)
            if name not in tools:
                return types.CallToolResult(
                    content=[types.TextContent(
                        type="text",
                        text="MCP tool is unavailable or disabled",
                    )],
                    isError=True,
                )
            return _call_result(await self.mcp_manager.call_tool(name, arguments))

    @asynccontextmanager
    async def run(self):
        manager = StreamableHTTPSessionManager(
            app=self.server,
            json_response=True,
            stateless=True,
        )
        self._session_manager = manager
        try:
            async with manager.run():
                yield
        finally:
            self._session_manager = None

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            response = JSONResponse({"error": "Unsupported transport"}, status_code=400)
            await response(scope, receive, send)
            return

        state = scope.get("state") or {}
        if not state.get("api_token"):
            response = JSONResponse({"error": "API token required"}, status_code=403)
            await response(scope, receive, send)
            return

        scopes = frozenset(state.get("api_token_scopes") or ())
        if not scopes.intersection({MCP_READ_SCOPE, MCP_CALL_SCOPE}):
            response = JSONResponse(
                {"error": f"API token missing required scope: {MCP_READ_SCOPE}"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        if self._session_manager is None:
            response = JSONResponse({"error": "MCP bridge is starting"}, status_code=503)
            await response(scope, receive, send)
            return

        token = _request_scopes.set(scopes)
        try:
            await self._session_manager.handle_request(scope, receive, send)
        finally:
            _request_scopes.reset(token)
