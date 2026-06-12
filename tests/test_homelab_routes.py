import json
from types import SimpleNamespace
from unittest.mock import patch, mock_open

import pytest
from fastapi import HTTPException

from routes.homelab_routes import (
    _load_services,
    _check_service_health,
    setup_homelab_routes,
)

def _request(*, api_token=True, scopes=None, owner="alice", current_user="browser-user"):
    state = SimpleNamespace(
        api_token=api_token,
        api_token_scopes=scopes or [],
        api_token_owner=owner,
        current_user=current_user,
    )
    return SimpleNamespace(state=state, app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)))

def _endpoint(router, path: str, method: str):
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route not found: {method} {path}")

def test_load_services_returns_empty_when_file_missing(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda path: False)
    services = _load_services()
    assert services == []

def test_load_services_loads_valid_config(monkeypatch):
    monkeypatch.setattr("os.path.exists", lambda path: True)
    mock_data = '{"services": [{"name": "pihole", "host": "1.2.3.4"}]}'
    with patch("builtins.open", mock_open(read_data=mock_data)):
        services = _load_services()
    assert len(services) == 1
    assert services[0]["name"] == "pihole"

@pytest.mark.asyncio
async def test_check_service_health_docker(monkeypatch):
    class MockCompletedProcess:
        def __init__(self, returncode, stdout):
            self.returncode = returncode
            self.stdout = stdout
            
    def mock_subprocess_run(*args, **kwargs):
        if "running_container" in args[0]:
            return MockCompletedProcess(0, "running\n")
        elif "stopped_container" in args[0]:
            return MockCompletedProcess(0, "exited\n")
        else:
            return MockCompletedProcess(1, "")

    monkeypatch.setattr("subprocess.run", mock_subprocess_run)
    
    res1 = await _check_service_health({"name": "test1", "container": "running_container"})
    assert res1["status"] == "ok"
    assert res1["container_status"] == "running"
    
    res2 = await _check_service_health({"name": "test2", "container": "stopped_container"})
    assert res2["status"] == "error"
    assert res2["container_status"] == "exited"

@pytest.mark.asyncio
async def test_check_service_health_http(monkeypatch):
    class MockResponse:
        def __init__(self, status_code):
            self.status_code = status_code

    class MockAsyncClient:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass
        async def get(self, url):
            if "fail" in url:
                raise Exception("connection refused")
            return MockResponse(200 if "ok" in url else 500)

    monkeypatch.setattr("httpx.AsyncClient", MockAsyncClient)

    res1 = await _check_service_health({"name": "test1", "health_url": "http://ok.local"})
    assert res1["status"] == "ok"
    assert res1["http_status"] == 200

    res2 = await _check_service_health({"name": "test2", "url": "http://error.local"})
    assert res2["status"] == "error"
    assert res2["http_status"] == 500

    res3 = await _check_service_health({"name": "test3", "health_url": "http://fail.local"})
    assert res3["status"] == "error"
    assert res3["http_status"] == "unreachable"

@pytest.mark.asyncio
async def test_list_services_requires_scope(monkeypatch):
    router = setup_homelab_routes()
    list_services = _endpoint(router, "/api/homelab/services", "GET")
    
    with pytest.raises(HTTPException) as exc:
        await list_services(_request(scopes=["chat"]))
    assert exc.value.status_code == 403
    assert "homelab:read" in exc.value.detail

@pytest.mark.asyncio
async def test_get_service_returns_404_if_missing(monkeypatch):
    router = setup_homelab_routes()
    get_service = _endpoint(router, "/api/homelab/services/{name}", "GET")
    monkeypatch.setattr("routes.homelab_routes._load_services", lambda: [])
    
    with pytest.raises(HTTPException) as exc:
        await get_service(_request(scopes=["homelab:read"]), name="missing")
    assert exc.value.status_code == 404

@pytest.mark.asyncio
async def test_health_returns_structured_json(monkeypatch):
    router = setup_homelab_routes()
    health = _endpoint(router, "/api/homelab/health", "GET")
    
    def mock_load_services():
        return [{"name": "s1", "container": "c1"}, {"name": "s2", "url": "http://ok.local"}]
        
    async def mock_check_service_health(srv):
        if srv["name"] == "s1":
            return {"name": "s1", "status": "error", "container_status": "exited"}
        return {"name": "s2", "status": "ok", "http_status": 200}

    monkeypatch.setattr("routes.homelab_routes._load_services", mock_load_services)
    monkeypatch.setattr("routes.homelab_routes._check_service_health", mock_check_service_health)

    data = await health(_request(scopes=["homelab:read"]))
    assert data["status"] == "error"
    assert len(data["services"]) == 2
    assert data["services"][0]["status"] == "error"
    assert data["services"][1]["status"] == "ok"
