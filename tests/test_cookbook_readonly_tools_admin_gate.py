"""Read-only Cookbook tools must be admin-only for the agent.

Regression: `list_cached_models`, `list_served_models`, `tail_serve_output`,
`list_downloads`, `search_hf_models`, `list_serve_presets` and
`list_cookbook_servers` sat in the plan-mode read-only allowlist but not in
NON_ADMIN_BLOCKED_TOOLS or the dispatcher's _ADMIN_TOOLS. Their
implementations call require_admin routes over the internal loopback with the
process-wide INTERNAL_TOOL_TOKEN and never look at the owner, so a non-admin
user's agent could reach admin capability through them. The worst case is
`list_cached_models`: a caller-chosen `host` is forwarded to
/api/model/cached, which SSHes to that host with the app user's keys and
walks caller-chosen `model_dir`s. THREAT_MODEL.md lists model serving as
admin-only, so the read side has to be gated the same way as serve/download.
"""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.tool_security import (
    NON_ADMIN_BLOCKED_TOOLS,
    blocked_tools_for_owner,
    delegated_credential_blocked_tools,
)
from src.tool_execution import _ADMIN_TOOLS

READONLY_COOKBOOK_TOOLS = (
    "list_cached_models",
    "list_served_models",
    "tail_serve_output",
    "list_downloads",
    "search_hf_models",
    "list_serve_presets",
    "list_cookbook_servers",
)


def _install_core_auth_stub(monkeypatch, *, is_admin: bool):
    """Narrow auth surface: AUTH enabled, configured, owner admin or not."""
    core_mod = types.ModuleType("core")
    core_mod.__path__ = []
    auth_mod = types.ModuleType("core.auth")

    class FakeAuth:
        is_configured = True

        def is_admin(self, username):
            return is_admin

    auth_mod.AuthManager = lambda: FakeAuth()
    core_mod.auth = auth_mod
    monkeypatch.setitem(sys.modules, "core", core_mod)
    monkeypatch.setitem(sys.modules, "core.auth", auth_mod)
    monkeypatch.setattr("src.auth_helpers._auth_disabled", lambda: False)
    return auth_mod


def test_readonly_cookbook_tools_are_in_every_admin_gate():
    # Spelled out (not imported from a shared tuple in src/) so dropping one
    # from either set fails here instead of silently reopening the hole.
    for tool in READONLY_COOKBOOK_TOOLS:
        assert tool in NON_ADMIN_BLOCKED_TOOLS, tool
        assert tool in _ADMIN_TOOLS, tool


def test_readonly_cookbook_tools_hidden_from_non_admin_and_bearer_runs(monkeypatch):
    _install_core_auth_stub(monkeypatch, is_admin=False)
    hidden = blocked_tools_for_owner("regular-user")
    for tool in READONLY_COOKBOOK_TOOLS:
        assert tool in hidden, tool
        assert tool in delegated_credential_blocked_tools(), tool


def test_admin_owner_keeps_readonly_cookbook_tools(monkeypatch):
    _install_core_auth_stub(monkeypatch, is_admin=True)
    assert blocked_tools_for_owner("admin-user") == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", READONLY_COOKBOOK_TOOLS)
async def test_non_admin_agent_cannot_execute_readonly_cookbook_tools(monkeypatch, tool_name):
    _install_core_auth_stub(monkeypatch, is_admin=False)
    from src.tool_execution import NO_TOOL_SECURITY_CONTEXT, execute_tool_block

    # The loopback must never be touched: if the gate fails, the tool would
    # hit a require_admin route with the internal token.
    import httpx

    def _boom(*args, **kwargs):
        raise AssertionError(f"{tool_name} reached the internal loopback for a non-admin owner")

    monkeypatch.setattr(httpx, "AsyncClient", _boom)
    monkeypatch.setattr(httpx, "get", _boom)
    monkeypatch.setattr(httpx, "post", _boom)

    payload = '{"host": "root@10.0.0.5", "ssh_port": "22", "model_dir": "/root,/etc"}'
    desc, result = await execute_tool_block(
        SimpleNamespace(tool_type=tool_name, content=payload),
        owner="regular-user",
        security_context=NO_TOOL_SECURITY_CONTEXT,
    )
    assert desc == f"{tool_name}: BLOCKED"
    assert result["exit_code"] == 1
    assert "admin" in result["error"]
