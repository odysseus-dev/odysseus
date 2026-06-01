"""
Streamable-HTTP MCP transport routing + header passthrough.

Covers the backend McpManager support for hosted/remote MCP servers:

  H1 — connect_server routes transport "http" / "streamable-http" /
       "streamable_http" to _connect_http, passing the url + auth headers.
  H2 — connect_server routes transport "sse" to _connect_sse WITH headers
       (so authed SSE servers keep working).
  H3 — an unknown transport returns False (and never touches a transport
       client).
  H4 — the REAL _connect_http unpacks the streamablehttp_client 3-tuple
       (read, write, get_session_id) correctly, while _connect_sse keeps
       unpacking the 2-tuple — and both forward the url + headers to their
       respective clients.

No network is required: mcp.client.streamable_http.streamablehttp_client,
mcp.client.sse.sse_client, and mcp.ClientSession are mocked in sys.modules.
"""

import sys
import types
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

# Sibling test modules stub heavy deps (and `src.*`) as bare MagicMocks in
# sys.modules to avoid loading the full app stack. Under a full-suite run
# (`pytest tests/`) those stubs linger; if `src.mcp_manager` were a lingering
# Mock we'd be asserting against a Mock instead of the real implementation.
# Evict any Mock-backed entry so we re-import the REAL module regardless of
# collection order. Keeps this file passing both in isolation AND full-suite.
for _name in ("src.mcp_manager",):
    _existing = sys.modules.get(_name)
    if isinstance(_existing, MagicMock):
        del sys.modules[_name]

from src.mcp_manager import McpManager  # noqa: E402


# ── shared mcp-module fixtures ────────────────────────────────────────────────

def _install_mcp_stubs(monkeypatch, *, http_tuple, sse_tuple):
    """Install fake mcp / mcp.client.* modules and return the call-recorders.

    streamablehttp_client yields ``http_tuple`` (a 3-tuple), sse_client yields
    ``sse_tuple`` (a 2-tuple). Both record the (url, headers) they were called
    with. ClientSession is an async-context-manager whose initialize/list_tools
    are awaitable no-ops returning an empty tool list.
    """
    calls = {"http": [], "sse": []}

    @asynccontextmanager
    async def fake_streamablehttp_client(url, headers=None):
        calls["http"].append({"url": url, "headers": headers})
        yield http_tuple

    @asynccontextmanager
    async def fake_sse_client(url, headers=None):
        calls["sse"].append({"url": url, "headers": headers})
        yield sse_tuple

    # ClientSession(read, write) used as an async context manager.
    class FakeClientSession:
        def __init__(self, read, write):
            self.read = read
            self.write = write
            self.initialize = AsyncMock()
            _tools_result = MagicMock()
            _tools_result.tools = []
            self.list_tools = AsyncMock(return_value=_tools_result)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    mcp_mod = types.ModuleType("mcp")
    mcp_mod.ClientSession = FakeClientSession
    mcp_mod.StdioServerParameters = MagicMock()

    client_mod = types.ModuleType("mcp.client")

    sse_mod = types.ModuleType("mcp.client.sse")
    sse_mod.sse_client = fake_sse_client

    http_mod = types.ModuleType("mcp.client.streamable_http")
    http_mod.streamablehttp_client = fake_streamablehttp_client

    stdio_mod = types.ModuleType("mcp.client.stdio")
    stdio_mod.stdio_client = MagicMock()

    monkeypatch.setitem(sys.modules, "mcp", mcp_mod)
    monkeypatch.setitem(sys.modules, "mcp.client", client_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.streamable_http", http_mod)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_mod)

    return calls


# ── H1 + H2 + H3: connect_server routing ──────────────────────────────────────

@pytest.mark.parametrize("transport", ["http", "streamable-http", "streamable_http"])
async def test_connect_server_routes_http_transports_with_headers(transport):
    mgr = McpManager()
    mgr._connect_http = AsyncMock(return_value=True)
    mgr._connect_sse = AsyncMock(return_value=True)

    headers = {"Authorization": "Bearer tok-123"}
    ok = await mgr.connect_server(
        server_id="s1", name="remote", transport=transport,
        url="https://mcp.example.com/", headers=headers,
    )

    assert ok is True
    mgr._connect_sse.assert_not_awaited()
    mgr._connect_http.assert_awaited_once_with("s1", "remote", "https://mcp.example.com/", headers)


async def test_connect_server_routes_sse_with_headers():
    mgr = McpManager()
    mgr._connect_http = AsyncMock(return_value=True)
    mgr._connect_sse = AsyncMock(return_value=True)

    headers = {"Authorization": "Bearer sse-tok"}
    ok = await mgr.connect_server(
        server_id="s2", name="sse-remote", transport="sse",
        url="https://sse.example.com/", headers=headers,
    )

    assert ok is True
    mgr._connect_http.assert_not_awaited()
    mgr._connect_sse.assert_awaited_once_with("s2", "sse-remote", "https://sse.example.com/", headers)


async def test_connect_server_sse_defaults_headers_to_empty_dict():
    # When no headers are supplied the manager still forwards a dict (not None)
    # so the transport helpers can treat "no headers" uniformly.
    mgr = McpManager()
    mgr._connect_sse = AsyncMock(return_value=True)

    await mgr.connect_server(server_id="s3", name="n", transport="sse", url="https://x/")

    mgr._connect_sse.assert_awaited_once_with("s3", "n", "https://x/", {})


async def test_connect_server_unknown_transport_returns_false():
    mgr = McpManager()
    mgr._connect_http = AsyncMock(return_value=True)
    mgr._connect_sse = AsyncMock(return_value=True)
    mgr._connect_stdio = AsyncMock(return_value=True)

    ok = await mgr.connect_server(
        server_id="bad", name="bad", transport="carrier-pigeon", url="https://x/",
    )

    assert ok is False
    mgr._connect_http.assert_not_awaited()
    mgr._connect_sse.assert_not_awaited()
    mgr._connect_stdio.assert_not_awaited()


# ── H4: real transport helpers unpack their tuples + forward url/headers ───────

async def test_real_connect_http_unpacks_3_tuple_and_passes_headers(monkeypatch):
    read, write, get_session_id = MagicMock(name="read"), MagicMock(name="write"), MagicMock(name="get_sid")
    calls = _install_mcp_stubs(
        monkeypatch,
        http_tuple=(read, write, get_session_id),  # streamable-http 3-tuple
        sse_tuple=(MagicMock(), MagicMock()),
    )

    mgr = McpManager()
    headers = {"Authorization": "Bearer http-tok"}
    ok = await mgr.connect_server(
        server_id="h1", name="http-srv", transport="http",
        url="https://mcp.example.com/mcp", headers=headers,
    )

    assert ok is True
    # url + headers forwarded to streamablehttp_client
    assert calls["http"] == [{"url": "https://mcp.example.com/mcp", "headers": headers}]
    assert calls["sse"] == []
    # 3-tuple unpacked: ClientSession got (read, write), get_session_id ignored
    session = mgr._sessions["h1"]
    assert session.read is read
    assert session.write is write
    assert mgr.get_server_status("h1")["status"] == "connected"
    assert mgr.get_server_status("h1")["transport"] == "http"


async def test_real_connect_sse_unpacks_2_tuple_and_passes_headers(monkeypatch):
    read, write = MagicMock(name="sse-read"), MagicMock(name="sse-write")
    calls = _install_mcp_stubs(
        monkeypatch,
        http_tuple=(MagicMock(), MagicMock(), MagicMock()),
        sse_tuple=(read, write),  # SSE 2-tuple
    )

    mgr = McpManager()
    headers = {"Authorization": "Bearer sse-tok"}
    ok = await mgr.connect_server(
        server_id="se1", name="sse-srv", transport="sse",
        url="https://sse.example.com/sse", headers=headers,
    )

    assert ok is True
    assert calls["sse"] == [{"url": "https://sse.example.com/sse", "headers": headers}]
    assert calls["http"] == []
    session = mgr._sessions["se1"]
    assert session.read is read
    assert session.write is write
    assert mgr.get_server_status("se1")["status"] == "connected"
    assert mgr.get_server_status("se1")["transport"] == "sse"
