import asyncio
from unittest.mock import patch

from src.mcp_manager import _format_mcp_connection_error, McpManager


def test_playwright_mcp_connection_error_includes_install_hint():
    msg = _format_mcp_connection_error(
        "Browser (Playwright)",
        "npx",
        ["-y", "@playwright/mcp@latest", "--headless"],
        RuntimeError("package not found"),
    )

    assert "package not found" in msg
    assert "Browser MCP could not start" in msg
    assert "npx -y @playwright/mcp@latest --version" in msg
    assert "restart Odysseus" in msg


def test_generic_mcp_connection_error_preserves_original_error():
    msg = _format_mcp_connection_error(
        "Custom MCP",
        "python",
        ["server.py"],
        RuntimeError("boom"),
    )

    assert msg == "boom"


def test_http_transport_routes_to_start_http_connect():
    mgr = McpManager()

    async def fake_start(server_id, name, url):
        return "ROUTED"

    with patch.object(McpManager, "_start_http_connect", side_effect=fake_start) as m:
        result = asyncio.run(mgr.connect_server("id1", "n", "http", url="https://x/mcp"))
    assert result == "ROUTED"
    m.assert_called_once()


def _patch_http_transport(monkeypatch, initialize):
    """Fake the Streamable HTTP transport and record, for each close, whether
    it ran in the task that opened it (AnyIO requires that)."""
    import contextlib
    import mcp
    import mcp.client.streamable_http as streamable_http
    import src.mcp_oauth as mcp_oauth

    closes = []

    @contextlib.asynccontextmanager
    async def fake_client(url, auth=None):
        opener = asyncio.current_task()
        try:
            yield (object(), object(), lambda: None)
        finally:
            closes.append(asyncio.current_task() is opener)

    class FakeSession:
        def __init__(self, *a):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    FakeSession.initialize = initialize
    monkeypatch.setattr(streamable_http, "streamablehttp_client", fake_client)
    monkeypatch.setattr(mcp, "ClientSession", FakeSession)
    monkeypatch.setattr(mcp_oauth, "build_provider", lambda *a, **k: None)
    return closes


def test_failed_http_connect_closes_transport_in_its_own_task(monkeypatch):
    """#5518: a failed connect left its transport for the GC, which exited the
    AnyIO cancel scope from the wrong task and kept the event loop spinning."""
    async def initialize(self):
        raise RuntimeError("Session terminated")

    closes = _patch_http_transport(monkeypatch, initialize)
    mgr = McpManager()

    ok = asyncio.run(mgr._start_http_connect("s1", "remote", "http://x/mcp/", wait=2))

    assert ok is False
    assert mgr._connections["s1"]["status"] == "error"
    assert "Session terminated" in mgr._connections["s1"]["error"]
    assert closes == [True]
    assert "s1" not in mgr._stacks


def test_cancelled_http_connect_closes_transport(monkeypatch):
    """Deleting a server mid-connect cancels the connect task; the transport
    must still be closed rather than leaked."""
    async def initialize(self):
        await asyncio.sleep(3600)

    closes = _patch_http_transport(monkeypatch, initialize)
    mgr = McpManager()

    async def run():
        await mgr._start_http_connect("s1", "remote", "http://x/mcp/", wait=0.05)
        await mgr.disconnect_server("s1")
        await asyncio.sleep(0)

    asyncio.run(run())
    assert closes == [True]
