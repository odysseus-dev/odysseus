import socket
from unittest.mock import AsyncMock

import pytest

import routes.cookbook_routes as cookbook_routes
from src.host_docker_access import HOST_DOCKER_ACCESS_HINT


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
