import pytest

import app as app_module


@pytest.mark.asyncio
async def test_runtime_info_reports_host_network_container_context(monkeypatch):
    original_exists = app_module.os.path.exists

    def fake_exists(path):
        if path == "/.dockerenv":
            return True
        return original_exists(path)

    monkeypatch.setattr(app_module.os.path, "exists", fake_exists)
    monkeypatch.setenv("ODYSSEUS_CONTAINER_NETWORK_MODE", "host")

    result = await app_module.runtime_info()

    assert result["in_docker"] is True
    assert result["container"]["engine"] == "docker"
    assert result["container"]["networkMode"] == "host"
    assert result["container"]["hostAccess"] is True
    assert result["container"]["hostGatewayReachable"] is False


@pytest.mark.asyncio
async def test_runtime_info_reports_docker_host_gateway_reachability(monkeypatch):
    original_exists = app_module.os.path.exists

    def fake_exists(path):
        if path == "/.dockerenv":
            return True
        return original_exists(path)

    monkeypatch.setattr(app_module.os.path, "exists", fake_exists)
    monkeypatch.setenv("ODYSSEUS_CONTAINER_NETWORK_MODE", "bridge")
    monkeypatch.setattr(
        app_module.socket,
        "getaddrinfo",
        lambda host, port: [("family", "type", "proto", "canon", ("172.18.0.1", 0))]
        if host == "host.docker.internal"
        else [],
    )

    result = await app_module.runtime_info()

    assert result["container"]["hostAccess"] is False
    assert result["container"]["hostGatewayReachable"] is True
    assert result["container"]["hostGatewayAddress"] == "172.18.0.1"


@pytest.mark.asyncio
async def test_runtime_info_reports_default_gateway_address_when_host_dns_missing(monkeypatch):
    original_exists = app_module.os.path.exists

    def fake_exists(path):
        if path == "/.dockerenv":
            return True
        return original_exists(path)

    monkeypatch.setattr(app_module.os.path, "exists", fake_exists)
    monkeypatch.setenv("ODYSSEUS_CONTAINER_NETWORK_MODE", "bridge")
    monkeypatch.setattr(
        app_module.socket,
        "getaddrinfo",
        lambda host, port: (_ for _ in ()).throw(OSError("missing host gateway")),
    )
    monkeypatch.setattr(app_module, "_docker_default_gateway_ip", lambda: "172.18.0.1")

    result = await app_module.runtime_info()

    assert result["container"]["hostAccess"] is False
    assert result["container"]["hostGatewayReachable"] is False
    assert result["container"]["hostGatewayAddress"] == "172.18.0.1"


@pytest.mark.asyncio
async def test_runtime_info_reports_agent_command_capabilities(monkeypatch):
    available = {
        "ip",
        "ss",
        "arp",
        "nmap",
        "ssh",
        "git",
        "docker",
    }

    monkeypatch.setattr(
        app_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in available else None,
    )

    result = await app_module.runtime_info()

    commands = result["commands"]
    assert commands["ip"] is True
    assert commands["ss"] is True
    assert commands["arp"] is True
    assert commands["nmap"] is True
    assert commands["ssh"] is True
    assert commands["git"] is True
    assert commands["docker"] is True
    assert result["capabilities"]["networkInspection"] is True
    assert result["capabilities"]["lanScan"] is True
    assert result["capabilities"]["sshClient"] is True
    assert result["capabilities"]["git"] is True
    assert result["capabilities"]["dockerClient"] is True
