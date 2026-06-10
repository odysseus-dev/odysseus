"""do_adopt_served_model must register the chat endpoint with the key the
endpoint manager actually reads.

do_manage_endpoints' "add" action reads args["base_url"]; the adopt tool used
to send "endpoint_url" (plus an unused "is_local"), so every adopt with the
default add_endpoint=True hit `base_url is required` and silently skipped
endpoint registration. This test pins the payload contract.
"""
import json

import pytest

import src.tool_implementations as ti


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200
        self.headers = {"content-type": "application/json"}
        self.text = ""

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient for the tool's loopback calls."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        if url.endswith("/api/shell/exec"):
            cmd = (json or {}).get("command", "")
            if "has-session" in cmd:
                return _FakeResp({"exit_code": 0, "stdout": "", "stderr": ""})
            return _FakeResp({"stdout": '{"data": []}'})  # health probe
        if url.endswith("/api/cookbook/state"):
            return _FakeResp({})
        return _FakeResp({})

    async def get(self, url, headers=None):
        if url.endswith("/api/cookbook/state"):
            return _FakeResp({"tasks": []})
        return _FakeResp({})


def test_adopt_registers_endpoint_with_base_url_key(monkeypatch):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    captured = {}

    async def _fake_manage_endpoints(content, owner=None):
        captured["payload"] = json.loads(content)
        captured["owner"] = owner
        return {"response": "Added endpoint", "exit_code": 0}

    monkeypatch.setattr(ti, "do_manage_endpoints", _fake_manage_endpoints)

    content = json.dumps(
        {"tmux_session": "srv", "model": "org/Model-7B", "port": 8001}
    )

    import asyncio

    result = asyncio.run(ti.do_adopt_served_model(content, owner="alice"))

    payload = captured["payload"]
    # The endpoint manager only reads base_url — that key must be present and
    # correct, and the obsolete keys must be gone.
    assert payload["base_url"] == "http://localhost:8001/v1"
    assert "endpoint_url" not in payload
    assert "is_local" not in payload
    assert captured["owner"] == "alice"
    # The tool should report success, not the "registration skipped" path.
    assert "skipped" not in result.get("output", "").lower()
