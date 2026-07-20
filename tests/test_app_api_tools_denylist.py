"""Regression: the generic app_api bridge must not rewrite the global tool
denylist. manage_settings already refuses enable_tool, but POST /api/tools
writes the same disabled_tools list and its require_admin is satisfied by the
loopback identity app_api rides, so the bridge has to fence the write too.
"""
import asyncio
import json

import httpx

from src.tools.system import do_app_api, _APP_API_BLOCKLIST_METHOD_PATH


def test_post_api_tools_is_blocked_before_loopback(monkeypatch):
    # Make any outbound loopback fail loudly, so this proves the block returns
    # before a request is issued rather than relying on the server to reject it.
    def _no_http(*args, **kwargs):
        raise AssertionError("app_api reached the network for a blocked write")

    monkeypatch.setattr(httpx, "AsyncClient", _no_http)

    result = asyncio.run(do_app_api(json.dumps({
        "path": "/api/tools", "method": "POST", "body": {"disabled": []},
    })))

    assert result.get("exit_code") == 1, result
    assert "settings" in result.get("error", "").lower(), result


def test_block_is_write_only():
    # Only the mutation is fenced; GET /api/tools (a read) stays callable.
    assert ("POST", "/api/tools") in _APP_API_BLOCKLIST_METHOD_PATH
    assert ("GET", "/api/tools") not in _APP_API_BLOCKLIST_METHOD_PATH
