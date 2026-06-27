import socket
from unittest.mock import AsyncMock

import pytest

from starlette.requests import Request

import routes.cookbook_routes as cookbook_routes
from routes.cookbook_helpers import ServeRequest
from src.host_docker_access import HOST_DOCKER_ACCESS_HINT


def _model_serve_endpoint():
    router = cookbook_routes.setup_cookbook_routes()
    for route in router.routes:
        if route.path == "/api/model/serve" and "POST" in route.methods:
            return route.endpoint
    raise AssertionError("POST /api/model/serve route not found")


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
async def test_container_cli_only_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes.shutil, "which", lambda binary: "/usr/bin/docker")

    available = await cookbook_routes._binary_available(
        "docker",
        None,
        None,
        in_container=True,
        environ={},
        socket_path=str(tmp_path / "missing.sock"),
    )

    assert available is False
    message = cookbook_routes._missing_binary_message(
        "docker",
        "local server",
        local_host_docker_blocked=True,
    )
    assert message == HOST_DOCKER_ACCESS_HINT
    assert "docker/host-docker.yml" in message


@pytest.mark.asyncio
async def test_container_opt_in_with_unix_socket_is_allowed(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes.shutil, "which", lambda binary: "/usr/bin/docker")
    socket_path = tmp_path / "docker.sock"

    with socket.socket(socket.AF_UNIX) as unix_socket:
        unix_socket.bind(str(socket_path))
        available = await cookbook_routes._binary_available(
            "docker",
            None,
            None,
            in_container=True,
            environ={"ODYSSEUS_ENABLE_HOST_DOCKER": "true"},
            socket_path=str(socket_path),
        )

    assert available is True


@pytest.mark.asyncio
async def test_native_local_docker_still_uses_cli_presence(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes.shutil, "which", lambda binary: "/usr/bin/docker")

    available = await cookbook_routes._binary_available(
        "docker",
        None,
        None,
        in_container=False,
        environ={},
        socket_path=str(tmp_path / "missing.sock"),
    )

    assert available is True


@pytest.mark.asyncio
async def test_remote_docker_still_uses_ssh_probe(monkeypatch):
    remote_probe = AsyncMock(return_value=True)
    monkeypatch.setattr(cookbook_routes, "_remote_binary_available", remote_probe)
    monkeypatch.setattr(
        cookbook_routes.shutil,
        "which",
        lambda binary: pytest.fail("remote checks must not inspect the local CLI"),
    )

    available = await cookbook_routes._binary_available(
        "docker",
        "gpu-server",
        "2222",
        windows=True,
        in_container=True,
        environ={},
        socket_path="/missing/docker.sock",
    )

    assert available is True
    remote_probe.assert_awaited_once_with(
        "gpu-server",
        "2222",
        "docker",
        windows=True,
    )


@pytest.mark.asyncio
async def test_local_container_serve_returns_host_docker_opt_in_hint(
    monkeypatch,
    tmp_path,
):
    async def binary_available(binary, remote, ssh_port, **kwargs):
        assert remote is None
        if binary == "tmux":
            return True
        assert cookbook_routes.shutil.which(binary) == "/usr/bin/docker"
        return False

    monkeypatch.setattr(cookbook_routes, "require_admin", lambda request: None)
    monkeypatch.setattr(cookbook_routes, "_binary_available", binary_available)
    monkeypatch.setattr(cookbook_routes, "running_in_container", lambda: True)
    monkeypatch.setattr(
        cookbook_routes,
        "host_docker_access_enabled",
        lambda: False,
    )
    monkeypatch.setattr(cookbook_routes.shutil, "which", lambda binary: "/usr/bin/docker")
    monkeypatch.setattr(cookbook_routes, "TMUX_LOG_DIR", tmp_path)
    monkeypatch.setattr(
        cookbook_routes,
        "load_stored_hf_token",
        lambda **kwargs: "",
    )

    response = await _model_serve_endpoint()(
        _admin_request(),
        ServeRequest(
            repo_id="example/model",
            cmd="python docker",
        ),
    )

    assert response["ok"] is False
    assert response["error"] == HOST_DOCKER_ACCESS_HINT
    assert "docker/host-docker.yml" in response["error"]


def test_local_ollama_docker_access_blocked_in_container_cli_only(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes.shutil, "which", lambda binary: "/usr/bin/docker")

    assert cookbook_routes._local_ollama_docker_access_blocked(
        in_container=True,
        environ={},
        socket_path=str(tmp_path / "missing.sock"),
    ) is True


def test_local_ollama_docker_access_not_blocked_for_native_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(cookbook_routes.shutil, "which", lambda binary: "/usr/bin/docker")

    assert cookbook_routes._local_ollama_docker_access_blocked(
        in_container=False,
        environ={},
        socket_path=str(tmp_path / "missing.sock"),
    ) is False


def test_local_ollama_download_probe_omits_docker_commands_when_blocked():
    lines = []

    cookbook_routes._append_local_ollama_download_command_lines(
        lines,
        "ollama pull llama3:latest",
        docker_fallback_available=False,
        docker_fallback_blocked=True,
    )

    rendered = "\n".join(lines)

    assert "command -v docker" not in rendered
    assert "docker ps" not in rendered
    assert "docker exec" not in rendered
    assert "ODYSSEUS_OLLAMA_PULL_CMD" in rendered
    assert "docker/host-docker.yml" in rendered
    assert "exit 127" in rendered


def test_local_ollama_download_probe_keeps_docker_fallback_when_allowed():
    lines = []

    cookbook_routes._append_local_ollama_download_command_lines(
        lines,
        "ollama pull llama3:latest",
        docker_fallback_available=True,
        docker_fallback_blocked=False,
    )

    rendered = "\n".join(lines)

    assert "docker ps" in rendered
    assert "docker exec ${ODYSSEUS_OLLAMA_CONTAINER}" in rendered
    assert "ODYSSEUS_OLLAMA_PULL_CMD" in rendered
