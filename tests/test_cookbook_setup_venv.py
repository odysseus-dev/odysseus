"""Tests for /api/cookbook/setup-venv — one-click venv creation on a server."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import routes.cookbook_routes as cookbook_routes


def _route_endpoint(path: str, method: str):
    router = cookbook_routes.setup_cookbook_routes()
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"{method} {path} route not found")


def _admin_request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/cookbook/setup-venv",
            "headers": [],
            "state": {},
        }
    )
    request.state.current_user = "admin"
    return request


@pytest.mark.asyncio
async def test_setup_venv_rejects_unsafe_path(monkeypatch):
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    endpoint = _route_endpoint("/api/cookbook/setup-venv", "POST")
    for bad in ("~/venv; rm -rf /", "path with spaces", "$(evil)", "-rf"):
        req = SimpleNamespace(host="alice@gpu-box", ssh_port=None, path=bad)
        with pytest.raises(HTTPException) as exc:
            await endpoint(_admin_request(), req)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_setup_venv_runs_wrapped_command_and_reports_path(monkeypatch):
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    captured = {}

    async def _fake_ssh(remote, ssh_port, remote_cmd, **kwargs):
        captured["remote"] = remote
        captured["cmd"] = remote_cmd
        return 0, b"pip 26.1.2 from /home/a/odysseus-venv/... (python 3.14)", b""

    monkeypatch.setattr(cookbook_routes, "run_ssh_command_async", _fake_ssh)
    endpoint = _route_endpoint("/api/cookbook/setup-venv", "POST")
    req = SimpleNamespace(host="alice@gpu-box", ssh_port=None, path=None)

    result = await endpoint(_admin_request(), req)

    assert result["ok"] is True
    assert result["path"] == "~/odysseus-venv"
    # sh -lc wrapper so the command survives non-POSIX remote login shells.
    assert captured["cmd"].startswith("sh -lc ")
    assert "python3 -m venv ~/odysseus-venv" in captured["cmd"]
    assert "~/odysseus-venv/bin/python3 -m pip --version" in captured["cmd"]


@pytest.mark.asyncio
async def test_setup_venv_surfaces_ensurepip_hint(monkeypatch):
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)

    async def _fake_ssh(remote, ssh_port, remote_cmd, **kwargs):
        return 1, b"", b"The virtual environment was not created successfully because ensurepip is not available."

    monkeypatch.setattr(cookbook_routes, "run_ssh_command_async", _fake_ssh)
    endpoint = _route_endpoint("/api/cookbook/setup-venv", "POST")
    req = SimpleNamespace(host="alice@gpu-box", ssh_port=None, path="~/venv")

    result = await endpoint(_admin_request(), req)

    assert result["ok"] is False
    assert "python3-venv" in result["error"]
