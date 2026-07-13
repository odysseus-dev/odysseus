import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

import src.mcp_manager as mcp_manager
from src.mcp_manager import McpManager, sanitize_mcp_error


SECRET = "super-secret-token"


class _TransportContext:
    def __init__(self, state, *, hang_enter=False, fail_enter=None):
        self.state = state
        self.hang_enter = hang_enter
        self.fail_enter = fail_enter

    async def __aenter__(self):
        self.state["transport_entered"] = True
        if self.fail_enter:
            raise self.fail_enter
        if self.hang_enter:
            await asyncio.Event().wait()
        return object(), object()

    async def __aexit__(self, exc_type, exc, tb):
        self.state["transport_closed"] = True


class _SessionContext:
    def __init__(self, state, read_stream, write_stream, *, hang_initialize=False):
        self.state = state
        self.hang_initialize = hang_initialize

    async def __aenter__(self):
        self.state["session_entered"] = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.state["session_closed"] = True

    async def initialize(self):
        self.state["initialized"] = True
        if self.hang_initialize:
            await asyncio.Event().wait()

    async def list_tools(self):
        tool = SimpleNamespace(
            name="echo",
            description="Echo tool",
            inputSchema={"type": "object", "properties": {}},
            annotations={"readOnlyHint": True},
        )
        return SimpleNamespace(tools=[tool])

    async def call_tool(self, tool_name, arguments):
        raise RuntimeError(
            "Authorization: Bearer "
            + SECRET
            + " url=https://user:pass@example.com/mcp?api_key="
            + SECRET
            + " password="
            + SECRET
        )


@pytest.fixture
def short_mcp_timeout(monkeypatch):
    monkeypatch.setattr(mcp_manager, "MCP_CONNECT_TIMEOUT_SECONDS", 0.01)


def _install_fake_mcp(monkeypatch, *, hang_enter=False, hang_initialize=False, fail_enter=None):
    state = {}
    mcp = types.ModuleType("mcp")
    mcp.ClientSession = lambda read, write: _SessionContext(
        state, read, write, hang_initialize=hang_initialize
    )
    mcp.StdioServerParameters = lambda command, args, env: SimpleNamespace(
        command=command, args=args, env=env
    )

    client = types.ModuleType("mcp.client")
    stdio = types.ModuleType("mcp.client.stdio")
    sse = types.ModuleType("mcp.client.sse")
    stdio.stdio_client = lambda params: _TransportContext(
        state, hang_enter=hang_enter, fail_enter=fail_enter
    )
    sse.sse_client = lambda url: _TransportContext(
        state, hang_enter=hang_enter, fail_enter=fail_enter
    )

    monkeypatch.setitem(sys.modules, "mcp", mcp)
    monkeypatch.setitem(sys.modules, "mcp.client", client)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio)
    monkeypatch.setitem(sys.modules, "mcp.client.sse", sse)
    return state


async def test_stdio_connection_times_out_and_cleans_partial_resources(monkeypatch, short_mcp_timeout):
    state = _install_fake_mcp(monkeypatch, hang_initialize=True)
    mgr = McpManager()

    ok = await mgr.connect_server(
        "srv", "Hanging stdio", "stdio", command="fake", args=[], env={}
    )

    assert ok is False
    assert mgr.get_server_status("srv")["status"] == "error"
    assert "timed out" in mgr.get_server_status("srv")["error"]
    assert state["session_closed"] is True
    assert state["transport_closed"] is True
    assert "srv" not in mgr._sessions
    assert "srv" not in mgr._stacks


async def test_sse_connection_times_out(monkeypatch, short_mcp_timeout):
    state = _install_fake_mcp(monkeypatch, hang_initialize=True)
    mgr = McpManager()

    ok = await mgr.connect_server("srv", "Hanging SSE", "sse", url="https://example.com/sse")

    assert ok is False
    assert mgr.get_server_status("srv")["status"] == "error"
    assert "timed out" in mgr.get_server_status("srv")["error"]
    assert state["transport_closed"] is True


async def test_builtin_reconnect_returns_after_connection_timeout(monkeypatch, short_mcp_timeout):
    _install_fake_mcp(monkeypatch, hang_initialize=True)
    import src.builtin_mcp as builtin_mcp

    monkeypatch.setitem(builtin_mcp._BUILTIN_SERVERS, "builtin_hang", ("server.py", "Hang"))
    monkeypatch.setattr(builtin_mcp, "builtin_python_env", lambda base_dir: {})

    mgr = McpManager()
    ok = await mgr._reconnect_builtin("builtin_hang")

    assert ok is False
    status = mgr.get_server_status("builtin_hang")
    assert status["status"] == "error"
    assert "timed out" in status["error"]


async def test_cancelled_stdio_connection_cleans_partial_resources(monkeypatch):
    state = _install_fake_mcp(monkeypatch, hang_initialize=True)
    monkeypatch.setattr(mcp_manager, "MCP_CONNECT_TIMEOUT_SECONDS", 3600)
    mgr = McpManager()

    task = asyncio.create_task(
        mgr.connect_server("srv", "Cancelled stdio", "stdio", command="fake", args=[], env={})
    )
    while not state.get("initialized"):
        await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert state["session_closed"] is True
    assert state["transport_closed"] is True
    assert "srv" not in mgr._sessions
    assert "srv" not in mgr._stacks


async def test_connection_status_api_and_logs_use_sanitized_errors(monkeypatch, caplog):
    raw = RuntimeError(
        "Authorization: Bearer "
        + SECRET
        + " token="
        + SECRET
        + " https://user:pass@example.com/mcp?api_key="
        + SECRET
        + "&ok=1"
    )
    _install_fake_mcp(monkeypatch, fail_enter=raw)
    mgr = McpManager()

    with caplog.at_level("ERROR", logger="src.mcp_manager"):
        ok = await mgr.connect_server("srv", "Leaky stdio", "stdio", command="fake", args=[], env={})

    status_error = mgr.get_server_status("srv")["error"]
    assert ok is False
    assert SECRET not in status_error
    assert "user:pass" not in status_error
    assert _has_redaction(status_error)
    assert SECRET not in caplog.text
    assert "user:pass" not in caplog.text
    assert _has_redaction(caplog.text)

    mgr._sessions["remote"] = _SessionContext({}, object(), object())
    api_result = await mgr.call_tool("mcp__remote__echo", {})

    assert api_result["exit_code"] == 1
    assert SECRET not in api_result["error"]
    assert "user:pass" not in api_result["error"]
    assert _has_redaction(api_result["error"])


@pytest.mark.parametrize(
    ("category", "raw", "forbidden", "expected"),
    [
        pytest.param(
            "authorization_bearer",
            "Authorization: Bearer " + SECRET,
            [SECRET],
            ["Authorization: [REDACTED]"],
            id="authorization_bearer",
        ),
        pytest.param(
            "authorization_basic",
            "Authorization: Basic " + SECRET,
            [SECRET],
            ["Authorization: [REDACTED]"],
            id="authorization_basic",
        ),
        pytest.param(
            "standalone_bearer",
            "Bearer " + SECRET,
            [SECRET],
            ["Bearer [REDACTED]"],
            id="standalone_bearer",
        ),
        pytest.param(
            "standalone_basic",
            "Basic " + SECRET,
            [SECRET],
            ["Basic [REDACTED]"],
            id="standalone_basic",
        ),
        pytest.param(
            "api_key_assignment",
            "API_KEY='" + SECRET + "'",
            [SECRET],
            ["API_KEY=[REDACTED]"],
            id="api_key_assignment",
        ),
        pytest.param(
            "token_assignment",
            "token=" + SECRET,
            [SECRET],
            ["token=[REDACTED]"],
            id="token_assignment",
        ),
        pytest.param(
            "password_assignment",
            "password=" + SECRET,
            [SECRET],
            ["password=[REDACTED]"],
            id="password_assignment",
        ),
        pytest.param(
            "secret_assignment",
            "client_secret=" + SECRET,
            [SECRET],
            ["client_secret=[REDACTED]"],
            id="secret_assignment",
        ),
        pytest.param(
            "url_userinfo",
            "https://user:pass@example.test:8443/mcp/path?safe=value&mode=ok#frag",
            ["user:pass"],
            ["https://[REDACTED]@example.test:8443/mcp/path?safe=value&mode=ok#frag"],
            id="url_userinfo",
        ),
        pytest.param(
            "url_sensitive_query",
            "https://example.test/mcp?token=" + SECRET + "&safe=value&mode=ok#frag",
            [SECRET],
            ["token=[REDACTED]", "safe=value", "mode=ok", "#frag"],
            id="url_sensitive_query",
        ),
    ],
)
def test_sanitizer_redacts_supported_secret_shapes(category, raw, forbidden, expected):
    text = sanitize_mcp_error(raw)

    for value in forbidden:
        if value in text:
            pytest.fail(f"{category} leaked a synthetic secret")
    for value in expected:
        if value not in text:
            pytest.fail(f"{category} missing expected sanitized output")
    if not _has_redaction(text):
        pytest.fail(f"{category} missing redaction marker")


async def test_successful_stdio_connection_still_registers_tools(monkeypatch):
    _install_fake_mcp(monkeypatch)
    mgr = McpManager()

    ok = await mgr.connect_server("srv", "Working stdio", "stdio", command="fake", args=[], env={})

    assert ok is True
    assert mgr.get_server_status("srv")["status"] == "connected"
    assert mgr.get_server_status("srv")["tool_count"] == 1
    assert mgr.get_all_tools()[0]["qualified_name"] == "mcp__srv__echo"
    await mgr.disconnect_server("srv")


def _has_redaction(text):
    return "[REDACTED]" in text
