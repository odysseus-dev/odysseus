"""CPU-only hardware results and SSH probe context agree with GPU paths."""

import json

import pytest

from services.hwfit import hardware

WINDOWS_PROBE = hardware._detect_windows


@pytest.fixture(autouse=True)
def probe(monkeypatch):
    monkeypatch.setattr(hardware, "_cache_by_host", {})
    for name in ("_remote_host", "_remote_port", "_remote_platform", "_last_gpu_error"):
        monkeypatch.setattr(hardware, name, None)
    monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 64.0)
    monkeypatch.setattr(hardware, "_get_available_ram_gb", lambda: 48.0)
    monkeypatch.setattr(hardware, "_get_cpu_count", lambda: 16)
    monkeypatch.setattr(hardware, "_get_cpu_name", lambda: "Test CPU")
    monkeypatch.setattr(hardware, "_get_cpu_arch", lambda: "x86_64")
    monkeypatch.setattr(hardware, "_is_containerized", lambda: False)
    for name in ("_detect_windows", "_detect_apple_silicon", "_detect_nvidia", "_detect_amd"):
        monkeypatch.setattr(hardware, name, lambda: None)


@pytest.mark.parametrize("arch,backend", [("x86_64", "cpu_x86"), ("arm64", "cpu_arm")])
@pytest.mark.parametrize("remote", [False, True])
def test_cpu_only_result_has_empty_gpu_inventory(monkeypatch, arch, backend, remote):
    monkeypatch.setattr(hardware, "_get_cpu_arch", lambda: arch)
    options = {"host": "user@cpu-fixture", "ssh_port": "2222", "platform": "linux"} if remote else {}
    result = hardware.detect_system(**options)

    assert result["has_gpu"] is False
    assert result["gpu_count"] == 0
    assert result["gpus"] == []
    assert result["gpu_groups"] == []
    assert result["homogeneous"] is True
    assert result["backend"] == backend
    assert result["cpu_arch"] == arch
    assert hardware.detect_system(**options) == result


def test_windows_cpu_only_result_has_empty_gpu_inventory(monkeypatch):
    monkeypatch.setattr(hardware, "_detect_windows", WINDOWS_PROBE)
    monkeypatch.setattr(hardware, "_run", lambda _command: json.dumps({
        "ram_gb": 64.0, "avail_gb": 48.0, "cpu_cores": 16,
        "cpu_name": "Test CPU", "cpu_arch": "AMD64", "gpu_count": 0,
    }))

    result = hardware.detect_system(host="user@windows-fixture", ssh_port="2222", platform="windows")

    assert result["has_gpu"] is False
    assert result["gpus"] == []
    assert result["gpu_groups"] == []
    assert result["homogeneous"] is True


@pytest.mark.parametrize("path", ["cpu", "gpu", "windows", "windows_failure", "connection_failure"])
def test_remote_probe_clears_all_connection_context(monkeypatch, path):
    gpu = {
        "gpu_name": "Test GPU", "gpu_vram_gb": 16.0, "gpu_count": 1,
        "gpus": [{"index": 0, "name": "Test GPU", "vram_gb": 16.0}],
        "gpu_groups": [{"name": "Test GPU", "count": 1}],
        "homogeneous": True, "backend": "cuda",
    }
    if path == "gpu":
        monkeypatch.setattr(hardware, "_detect_nvidia", lambda: gpu)
    elif path == "windows":
        monkeypatch.setattr(hardware, "_detect_windows", lambda: {"has_gpu": False, "backend": "cpu_x86"})
    elif path == "connection_failure":
        monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 0.0)

    result = hardware.detect_system(
        host="user@hardware-fixture", ssh_port="2222",
        platform="windows" if path.startswith("windows") else "linux", fresh=True,
    )

    if path in {"windows_failure", "connection_failure"}:
        assert result["error"]
    elif path == "gpu":
        assert result["gpus"] == gpu["gpus"]
        assert result["gpu_groups"] == gpu["gpu_groups"]
        assert result["backend"] == "cuda"
    else:
        assert result["has_gpu"] is False
    assert (hardware._remote_host, hardware._remote_port, hardware._remote_platform) == (None, None, None)
