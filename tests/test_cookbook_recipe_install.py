"""Recipe installs reach the real serve route without widening command authority."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_helpers import _validate_serve_cmd


@pytest.fixture
def recipe_app(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path / "runners")
    monkeypatch.setattr(cookbook_routes, "COOKBOOK_STATE_FILE", tmp_path / "cookbook.json")
    monkeypatch.setattr(cookbook_routes, "IS_WINDOWS", False)
    monkeypatch.setattr(cookbook_routes, "load_stored_hf_token", lambda **kwargs: "")
    monkeypatch.setattr(cookbook_routes, "_binary_available", AsyncMock(return_value=True))
    launches = []

    async def launch(command, **kwargs):
        launches.append(command)
        return SimpleNamespace(
            returncode=0,
            wait=AsyncMock(return_value=0),
            stderr=SimpleNamespace(read=AsyncMock(return_value=b"")),
        )

    monkeypatch.setattr(cookbook_routes.asyncio, "create_subprocess_shell", launch)
    import src.assistant_log
    monkeypatch.setattr(src.assistant_log, "log_to_assistant", lambda *args, **kwargs: None)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(
        is_configured=True, is_admin=lambda user: user == "admin",
    )

    @app.middleware("http")
    async def authenticated_user(request, call_next):
        request.state.current_user = request.headers.get("x-test-user", "admin")
        return await call_next(request)

    app.include_router(cookbook_routes.setup_cookbook_routes())
    return app, launches, tmp_path / "runners"


@pytest.mark.asyncio
@pytest.mark.parametrize(("backend", "cmd"), [
    ("vllm", "uv pip install -U vllm --torch-backend auto"),
    ("sglang", 'uv pip install -U "sglang[all]" --torch-backend auto'),
    ("llama_cpp", 'CMAKE_ARGS="-DGGML_CUDA=on" uv pip install -U "llama-cpp-python[server]"'),
    ("diffusers", '/opt/model-venv/bin/python3 -m pip install -U "diffusers[torch]" torchvision accelerate scipy python-multipart'),
    ("mlx_lm", "python -m pip install -U mlx-lm"),
    ("krea_diffusers", "python -m pip install -U git+https://github.com/huggingface/diffusers.git torchvision accelerate scipy python-multipart"),
])
@pytest.mark.parametrize("remote_host", [None, "gpu-server"])
async def test_recipe_install_starts_task_through_real_route(recipe_app, backend, cmd, remote_host):
    app, launches, runners = recipe_app
    payload = {"repo_id": backend, "cmd": cmd}
    if remote_host:
        payload.update(remote_host=remote_host, ssh_port="2222", env_prefix="source /opt/model-venv/bin/activate")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/api/model/serve", json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["ok"] is True
    assert data["endpoint_id"] is None, "dependency tasks must not register model endpoints"
    assert data["remote"] == (remote_host or "local")
    assert len(launches) == 1
    runner = (runners / f'{data["session_id"]}_run.sh').read_text(encoding="utf-8")
    assert "pip install --no-cache-dir" in runner
    assert "DOWNLOAD_OK" in runner
    if "uv pip install" in cmd:
        assert "uv pip install --no-cache-dir -U" in runner
    if "--torch-backend auto" in cmd:
        assert "--torch-backend auto" in runner
    if remote_host:
        assert 'source "/opt/model-venv/bin/activate"' in runner
        assert "gpu-server" in launches[0]
        assert "2222" in launches[0]


@pytest.mark.parametrize("cmd", [
    "uv pip install -U vllm --torch-backend auto",
    'CMAKE_ARGS="-DGGML_CUDA=on" uv pip install -U "llama-cpp-python[server]"',
    "'/opt/uv tools/bin/uv' pip install 'vllm>=1'",
])
def test_uv_pip_install_is_narrowly_supported(cmd):
    assert _validate_serve_cmd(cmd) == cmd


@pytest.mark.parametrize("cmd", [
    "uv run python -c 'print(1)'",
    "uv tool run arbitrary-command",
    "uv python install",
    "uv pip compile requirements.txt",
    "uv --directory /tmp pip install vllm",
    "uv pip install",
    "uv pip install vllm | sh",
    "uv pip install vllm > /tmp/install-output",
    "uv pip install vllm < /tmp/install-input",
    "uv pip install vllm & id",
    "uv pip install $PACKAGE",
    "uv pip install vllm && id",
    "uv pip install vllm; id",
    "uv pip install $(id)",
    "uv pip install `id`",
    "uv pip install vllm\nid",
])
def test_other_uv_execution_and_shell_syntax_remain_rejected(cmd):
    with pytest.raises(HTTPException) as exc:
        _validate_serve_cmd(cmd)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"repo_id": "vllm setup"},
    {"repo_id": "vllm;id"},
    {"remote_host": "--bad-host"},
    {"remote_host": "gpu-server", "ssh_port": "22;id"},
    {"cmd": "uv run python -c 'print(1)'"},
    {"cmd": "uv pip install vllm | sh"},
])
async def test_recipe_route_rejects_invalid_identity_target_or_command_before_launch(recipe_app, changes):
    app, launches, _ = recipe_app
    payload = {"repo_id": "vllm", "cmd": "uv pip install -U vllm --torch-backend auto", **changes}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/api/model/serve", json=payload)
    assert response.status_code == 400, response.text
    assert launches == []


@pytest.mark.asyncio
@pytest.mark.parametrize("user", ["regular-user", "api"])
@pytest.mark.parametrize("remote_host", [None, "gpu-server"])
async def test_uv_recipe_install_keeps_native_admin_gate(recipe_app, user, remote_host):
    app, launches, _ = recipe_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post(
            "/api/model/serve", headers={"x-test-user": user},
            json={"repo_id": "vllm", "cmd": "uv pip install -U vllm --torch-backend auto", "remote_host": remote_host},
        )
    assert response.status_code == 403, response.text
    assert launches == []
