import asyncio
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.cookbook_routes as cookbook_routes
import routes.cookbook_helpers as cookbook_helpers


def _build_client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    app = FastAPI()
    app.include_router(cookbook_routes.setup_cookbook_routes())
    return TestClient(app), tmp_path / "cookbook_state.json"


def _write_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _require_admin_allow(_request):
    pass


def test_cookbook_test_remote_no_admin(monkeypatch, tmp_path):
    client, _ = _build_client(monkeypatch, tmp_path)

    res = client.get("/api/cookbook/test-remote",
        params={"port": "2201"}
    )

    assert res.status_code == 403
    assert "Admin only" in res.text


def test_cookbook_test_remote_bad_request_without_host(monkeypatch, tmp_path):
    client, _ = _build_client(monkeypatch, tmp_path)

    monkeypatch.setattr(cookbook_routes, "require_admin", _require_admin_allow)

    res = client.get("/api/cookbook/test-remote",
        params={"port": "2201"}
    )

    assert res.status_code == 400
    assert "host is required" in res.text


def test_cookbook_test_remote_bad_connection(monkeypatch, tmp_path):
    client, _ = _build_client(monkeypatch, tmp_path)

    async def _fake_ssh_response(
        remote: str,
        ssh_port: str | None,
        remote_cmd: str,
        *,
        timeout: float,
        connect_timeout: int | None = None,
        strict_host_key_checking: bool | None = None,
        stdin_data: bytes | None = None,
    ):
        calls["remote"] = remote
        calls["ssh_port"] = ssh_port
        raise asyncio.TimeoutError("Connection timed out")

    calls = {}
    
    monkeypatch.setattr(cookbook_routes, "require_admin", _require_admin_allow)
    monkeypatch.setattr(cookbook_helpers, "create_ssh_session_async", _fake_ssh_response)

    res = client.get("/api/cookbook/test-remote",
        params={"host": "alice@gpu-box", "port": "2201"}
    )

    assert res.status_code == 200
    assert res.json()["ok"] is False
    assert res.json()["error"] == "SSH probe timed out"
    assert calls["remote"] == "alice@gpu-box"
    assert calls["ssh_port"] == "2201"


def test_cookbook_test_remote_good_connection(monkeypatch, tmp_path):
    client, state_path = _build_client(monkeypatch, tmp_path)
    _write_state(
        state_path,
        {
            "env": {
                "servers": [
                    {
                        "name": "alice",
                        "host": "alice@gpu-box",
                        "port": "2201",
                    }
                ]
            }
        },
    )

    async def _fake_probe(host, port, timeout=10.0):
        calls["host"] = host
        calls["port"] = port
        calls["timeout"] = timeout
        return {"ok": True, "error": "", "latency_ms": 7}

    calls = {}

    monkeypatch.setattr(cookbook_routes, "require_admin", _require_admin_allow)
    monkeypatch.setattr(cookbook_routes, "test_server_connection", _fake_probe)

    res = client.get(
        "/api/cookbook/test-remote",
        params={
            "host": "alice@gpu-box",
            "port": "2201",
        },
    )

    assert res.status_code == 200
    assert res.json()["ok"] is True
    assert calls["host"] == "alice@gpu-box"
    assert calls["port"] == "2201"
