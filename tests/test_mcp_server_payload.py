import importlib
import sys
import types
from unittest.mock import MagicMock

from tests.helpers.db_stubs import make_core_db_stub


def _load_mcp_routes(monkeypatch):
    make_core_db_stub(monkeypatch, models=["McpServer"])

    middleware = types.ModuleType("core.middleware")
    setattr(middleware, "require_admin", MagicMock())
    monkeypatch.setitem(sys.modules, "core.middleware", middleware)

    manager = types.ModuleType("src.mcp_manager")
    setattr(manager, "McpManager", MagicMock())
    monkeypatch.setitem(sys.modules, "src.mcp_manager", manager)

    sys.modules.pop("routes.mcp_routes", None)
    return importlib.import_module("routes.mcp_routes")


def test_json_mcp_server_payload_serializes_structured_fields(monkeypatch):
    mcp_routes = _load_mcp_routes(monkeypatch)

    payload = mcp_routes._coerce_server_payload(
        {
            "name": "Filesystem",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"],
            "env": {"NODE_ENV": "production"},
            "oauth_config": {"token_file": "~/.cache/token.json"},
        },
        name=None,
        transport="stdio",
        command=None,
        args="[]",
        env="{}",
        url=None,
        oauth_file=None,
        oauth_config=None,
    )

    assert payload["name"] == "Filesystem"
    assert payload["command"] == "npx"
    assert payload["args"] == '["-y", "@modelcontextprotocol/server-filesystem", "/data"]'
    assert payload["env"] == '{"NODE_ENV": "production"}'
    assert payload["oauth_config"] == '{"token_file": "~/.cache/token.json"}'


def test_json_mcp_server_payload_keeps_form_defaults(monkeypatch):
    mcp_routes = _load_mcp_routes(monkeypatch)

    payload = mcp_routes._coerce_server_payload(
        {"name": "SSE server", "transport": "sse", "url": "http://localhost:3000/sse"},
        name=None,
        transport="stdio",
        command=None,
        args="[]",
        env="{}",
        url=None,
        oauth_file=None,
        oauth_config=None,
    )

    assert payload["transport"] == "sse"
    assert payload["url"] == "http://localhost:3000/sse"
    assert payload["args"] == "[]"
    assert payload["env"] == "{}"
