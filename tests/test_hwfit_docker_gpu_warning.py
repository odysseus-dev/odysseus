"""Regression tests for Docker GPU passthrough hardware reporting."""

from services.hwfit import hardware


def test_container_without_gpu_passthrough_reports_gpu_error(monkeypatch):
    """Docker deployments should explain missing passthrough instead of plain No GPU."""
    monkeypatch.setattr(hardware.os.path, "exists", lambda p: p == "/.dockerenv")
    monkeypatch.delenv("NVIDIA_VISIBLE_DEVICES", raising=False)
    monkeypatch.setattr(hardware, "_run", lambda cmd: None)
    monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 19.4)
    monkeypatch.setattr(hardware, "_get_available_ram_gb", lambda: 17.6)
    monkeypatch.setattr(hardware, "_get_cpu_count", lambda: 8)
    monkeypatch.setattr(hardware, "_get_cpu_name", lambda: "Intel CPU")
    monkeypatch.setattr(hardware, "_detect_apple_silicon", lambda: None)
    monkeypatch.setattr(hardware, "_detect_nvidia", lambda: None)
    monkeypatch.setattr(hardware, "_detect_amd", lambda: None)

    system = hardware.detect_system(fresh=True)

    assert system["has_gpu"] is False
    assert system["gpu_error"]
    assert "Docker container" in system["gpu_error"]
    assert "GPU compose overlay" in system["gpu_error"]


def test_non_container_without_gpu_has_no_gpu_error(monkeypatch):
    """CPU-only bare-metal hosts should still render as plain No GPU."""
    monkeypatch.setattr(hardware.os.path, "exists", lambda p: False)
    monkeypatch.setattr(hardware, "_read_file", lambda path: "")
    monkeypatch.setattr(hardware, "_run", lambda cmd: None)
    monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 32.0)
    monkeypatch.setattr(hardware, "_get_available_ram_gb", lambda: 24.0)
    monkeypatch.setattr(hardware, "_get_cpu_count", lambda: 8)
    monkeypatch.setattr(hardware, "_get_cpu_name", lambda: "Intel CPU")
    monkeypatch.setattr(hardware, "_detect_apple_silicon", lambda: None)
    monkeypatch.setattr(hardware, "_detect_nvidia", lambda: None)
    monkeypatch.setattr(hardware, "_detect_amd", lambda: None)

    system = hardware.detect_system(fresh=True)

    assert system["has_gpu"] is False
    assert system["gpu_error"] is None


def test_broken_nvidia_overlay_reports_gpu_error(monkeypatch):
    """A configured but device-less NVIDIA overlay should still warn clearly."""
    monkeypatch.setattr(hardware.os.path, "exists", lambda p: p == "/.dockerenv")
    monkeypatch.setenv("NVIDIA_VISIBLE_DEVICES", "all")

    error = hardware._container_gpu_error()

    assert error
    assert "NVIDIA GPU compose overlay" in error
    assert "no NVIDIA device" in error


def test_visible_gpu_device_suppresses_container_gpu_error(monkeypatch):
    """When the container has a GPU device node, no passthrough warning is needed."""
    visible = {"/.dockerenv", "/dev/nvidiactl"}
    monkeypatch.setattr(hardware.os.path, "exists", lambda p: p in visible)
    monkeypatch.setenv("NVIDIA_VISIBLE_DEVICES", "all")

    assert hardware._container_gpu_error() is None


def test_physical_cpu_count_parses_hyperthreaded_linux_cpuinfo(monkeypatch):
    """The UI should be able to show 4C/8T instead of ambiguous '8 cores'."""
    cpuinfo = "\n\n".join(
        "\n".join(
            [
                f"processor\t: {idx}",
                "physical id\t: 0",
                f"core id\t\t: {idx // 2}",
                "cpu cores\t: 4",
                "siblings\t: 8",
            ]
        )
        for idx in range(8)
    )
    monkeypatch.setattr(hardware, "_read_file", lambda path: cpuinfo if path == "/proc/cpuinfo" else "")

    assert hardware._get_physical_cpu_count() == 4


def test_detect_system_reports_cpu_topology_and_docker_wsl_ram_scope(monkeypatch):
    """Detected hardware should distinguish usable Docker RAM from host hardware."""
    visible = {"/.dockerenv", "/dev/nvidiactl"}
    monkeypatch.setattr(hardware.os, "name", "posix", raising=False)
    monkeypatch.setattr(hardware.os.path, "exists", lambda p: p in visible)
    monkeypatch.setattr(
        hardware,
        "_read_file",
        lambda path: "Linux version 6.6.87.2-microsoft-standard-WSL2"
        if path == "/proc/version"
        else "",
    )
    monkeypatch.setenv("ODYSSEUS_HOST_TOTAL_RAM_GB", "40")
    monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 19.4)
    monkeypatch.setattr(hardware, "_get_available_ram_gb", lambda: 17.6)
    monkeypatch.setattr(hardware, "_get_cpu_count", lambda: 8)
    monkeypatch.setattr(hardware, "_get_physical_cpu_count", lambda: 4)
    monkeypatch.setattr(hardware, "_get_cpu_name", lambda: "Intel CPU")
    monkeypatch.setattr(hardware, "_detect_apple_silicon", lambda: None)
    monkeypatch.setattr(
        hardware,
        "_detect_nvidia",
        lambda: {
            "gpu_name": "NVIDIA RTX",
            "gpu_vram_gb": 4.0,
            "gpu_count": 1,
            "gpus": [],
            "gpu_groups": [],
            "homogeneous": True,
            "backend": "cuda",
        },
    )

    system = hardware.detect_system(fresh=True)

    assert system["cpu_logical_cores"] == 8
    assert system["cpu_physical_cores"] == 4
    assert system["total_ram_gb"] == 19.4
    assert system["host_total_ram_gb"] == 40.0
    assert system["runtime"] == "docker_wsl2"
    assert system["ram_scope"] == "docker_wsl2"
