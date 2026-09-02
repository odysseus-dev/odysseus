"""Only built-in MCP servers may inherit the full process environment.

`_connect_stdio` has to widen the environment for the built-in NPX browser
server, which otherwise loses PLAYWRIGHT_BROWSERS_PATH and reports
`Browser "firefox" is not installed`. Widening it for *every* stdio server
would bypass the MCP SDK's allowlist and hand the whole environment — which
carries ODYSSEUS_INTERNAL_TOKEN, an admin bypass, among other secrets — to
user-added servers, i.e. to an arbitrary third-party command.

These pin the split: full inheritance for built-ins, the SDK's filtered
default for everything else, with a server's own explicit env still applied
on top in both cases.
"""

import asyncio
from unittest.mock import patch

import pytest

from src.mcp_manager import McpManager


CANARY = "ODYSSEUS_TEST_CANARY_9931"


class _Captured(Exception):
    """Raised by the stub to stop _connect_stdio once params are captured."""


def _connect_and_capture(server_id, env=None):
    """Run _connect_stdio far enough to capture its StdioServerParameters."""
    seen = {}

    def _stub(params):
        seen["params"] = params
        raise _Captured

    mgr = McpManager()
    with patch("mcp.client.stdio.stdio_client", _stub):
        try:
            asyncio.run(
                mgr._connect_stdio(
                    server_id, "Test Server", "echo", ["hi"], env or {}
                )
            )
        except _Captured:
            pass  # expected: the stub aborts once it has the params
    assert "params" in seen, "stdio_client was never reached"
    return seen["params"].env


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv(CANARY, "canary-value")
    monkeypatch.setenv("ODYSSEUS_INTERNAL_TOKEN", "admin-bypass-token")


def test_builtin_server_inherits_full_environment():
    """Built-ins are our own code — they need the deployment's environment."""
    env = _connect_and_capture("builtin_browser")

    assert env[CANARY] == "canary-value"
    assert env["ODYSSEUS_INTERNAL_TOKEN"] == "admin-bypass-token"


def test_user_server_does_not_receive_unfiltered_environment():
    """A user-added server is a third-party process: no secrets by default."""
    env = _connect_and_capture("user_added_server")

    assert CANARY not in env
    assert "ODYSSEUS_INTERNAL_TOKEN" not in env


def test_user_server_still_gets_the_sdk_default_and_its_own_env():
    """Filtering must not starve the server of what it legitimately needs."""
    from mcp.client.stdio import get_default_environment

    env = _connect_and_capture("user_added_server", {"MY_SERVER_KEY": "abc"})

    for key in get_default_environment():
        assert key in env, f"SDK default {key} was dropped"
    assert env["MY_SERVER_KEY"] == "abc"
    assert CANARY not in env


def test_explicit_env_overrides_inherited_value_for_builtins():
    """An explicit per-server value still wins over the inherited one."""
    env = _connect_and_capture("builtin_browser", {CANARY: "explicit"})

    assert env[CANARY] == "explicit"
