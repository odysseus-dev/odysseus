import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_helpers import ServeRequest


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
            "path": "/api/model/serve",
            "headers": [],
            "state": {},
        }
    )
    request.state.current_user = "admin"
    return request


@pytest.mark.asyncio
async def test_remote_windows_diffusers_is_rejected_before_runner_launch(monkeypatch):
    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    calls = []

    async def fail_if_shell_runs(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("remote Windows Diffusers should fail before shell launch")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_if_shell_runs)

    endpoint = _route_endpoint("/api/model/serve", "POST")
    req = ServeRequest(
        repo_id="diffusers/example",
        cmd="python scripts/diffusion_server.py --model diffusers/example --port 8100",
        remote_host="winbox",
        platform="windows",
    )

    with pytest.raises(HTTPException) as exc:
        await endpoint(_admin_request(), req)

    assert exc.value.status_code == 400
    assert "Remote Windows Diffusers" in str(exc.value.detail)
    assert calls == []
