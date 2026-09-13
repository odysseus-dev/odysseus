import json

import httpx
import pytest

from src.tools import cookbook


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._data


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        ("llama-server --port=8001", 8001),
        ("llama-server -p 8002", 8002),
        ("OLLAMA_HOST='[::1]:11436' ollama serve", 11436),
        ("$env:OLLAMA_HOST = '0.0.0.0:11437'; ollama serve", 11437),
        ("ollama serve", 11434),
        ("docker exec ollama-test ollama-import org/model model 8192 model.gguf", 11434),
    ],
)
def test_infer_serve_port_matches_backend_command_forms(cmd, expected):
    assert cookbook._infer_serve_port(cmd) == expected


def _install_http_fake(monkeypatch, *, state, serve_response):
    writes = []
    endpoint_requests = []

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            assert url.endswith("/api/cookbook/state")
            return _FakeResponse(state)

        async def post(self, url, **kwargs):
            if url.endswith("/api/model/serve"):
                return _FakeResponse(serve_response)
            if url.endswith("/api/model-endpoints"):
                endpoint_requests.append(kwargs.get("data"))
                return _FakeResponse({"id": "endpoint-fallback"})
            if url.endswith("/api/cookbook/state"):
                writes.append(kwargs.get("json"))
                return _FakeResponse({"ok": True})
            raise AssertionError(f"Unexpected POST {url}")

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return writes, endpoint_requests


@pytest.mark.asyncio
async def test_serve_model_persists_backend_effective_metadata(monkeypatch):
    effective_cmd = "OLLAMA_HOST=0.0.0.0:11437 ollama serve"
    writes, endpoint_requests = _install_http_fake(
        monkeypatch,
        state={},
        serve_response={
            "ok": True,
            "session_id": "serve-agent",
            "effective_cmd": effective_cmd,
            "runtime_port": 11437,
        },
    )

    async def _resolve(host):
        return host

    async def _env(_host):
        return {"platform": "windows", "ssh_port": "2222"}

    monkeypatch.setattr(cookbook, "_resolve_cookbook_host", _resolve)
    monkeypatch.setattr(cookbook, "_cookbook_env_for_host", _env)

    result = await cookbook.do_serve_model(json.dumps({
        "repo_id": "org/model",
        "cmd": "ollama serve",
        "host": "gpu-box",
    }))

    assert result["exit_code"] == 0
    assert result["effective_cmd"] == effective_cmd
    assert result["runtime_port"] == 11437
    assert endpoint_requests[0]["base_url"] == "http://gpu-box:11437/v1"
    task = writes[-1]["tasks"][0]
    assert task["payload"] == {
        "repo_id": "org/model",
        "remote_host": "gpu-box",
        "_cmd": effective_cmd,
        "runtime_port": "11437",
        "platform": "windows",
        "ssh_port": "2222",
    }
    assert task["sshPort"] == "2222"
    assert task["platform"] == "windows"


@pytest.mark.asyncio
async def test_serve_preset_persists_backend_effective_metadata(monkeypatch):
    effective_cmd = "OLLAMA_HOST=0.0.0.0:11438 ollama serve"
    state = {
        "presets": [{
            "name": "ollama-model",
            "model": "org/model",
            "host": "gpu-box",
            "cmd": "ollama serve",
        }]
    }
    writes, _ = _install_http_fake(
        monkeypatch,
        state=state,
        serve_response={
            "ok": True,
            "session_id": "serve-preset",
            "endpoint_id": "endpoint-route",
            "effective_cmd": effective_cmd,
            "runtime_port": 11438,
        },
    )

    async def _env(_host):
        return {"platform": "windows", "ssh_port": "2200"}

    monkeypatch.setattr(cookbook, "_cookbook_env_for_host", _env)

    result = await cookbook.do_serve_preset(json.dumps({"name": "ollama-model"}))

    assert result["exit_code"] == 0
    assert result["effective_cmd"] == effective_cmd
    assert result["runtime_port"] == 11438
    task = writes[-1]["tasks"][0]
    assert task["payload"]["_cmd"] == effective_cmd
    assert task["payload"]["runtime_port"] == "11438"
    assert task["sshPort"] == "2200"
    assert task["platform"] == "windows"


@pytest.mark.asyncio
async def test_env_lookup_uses_local_host_platform_and_server_port(monkeypatch):
    state = {
        "env": {
            "hostPlatform": "windows",
            "servers": [{
                "host": "gpu-box",
                "platform": "windows",
                "port": "2222",
            }],
        }
    }
    _install_http_fake(monkeypatch, state=state, serve_response={})
    monkeypatch.setattr("routes.cookbook_helpers.load_stored_hf_token", lambda: "")

    local = await cookbook._cookbook_env_for_host("")
    remote = await cookbook._cookbook_env_for_host("gpu-box")

    assert local["platform"] == "windows"
    assert remote["platform"] == "windows"
    assert remote["ssh_port"] == "2222"
