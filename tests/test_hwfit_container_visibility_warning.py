"""Tests for Cookbook hardware probe context and container visibility warnings."""

import pytest

from services.hwfit import hardware


@pytest.mark.area_services
@pytest.mark.area_unit
def test_container_no_gpu_gets_visibility_warning(monkeypatch):
    """Warn when a containerized local probe cannot see a GPU."""
    monkeypatch.setattr(hardware, "_is_containerized", lambda: True)

    result = {
        "total_ram_gb": 7.7,
        "available_ram_gb": 6.4,
        "cpu_cores": 12,
        "cpu_name": "Test CPU",
        "has_gpu": False,
        "gpu_name": None,
        "gpu_vram_gb": None,
        "gpu_count": 0,
        "backend": "cpu_x86",
        "gpu_error": None,
    }

    out = hardware._attach_probe_context(result, host="")

    assert out["containerized"] is True
    assert out["probe_scope"] == "container"
    assert out["hardware_visibility_warning"]["code"] == "container_no_gpu_visible"
    assert "manual_hardware" in out["hardware_visibility_warning"]["actions"]


@pytest.mark.area_services
@pytest.mark.area_unit
def test_native_no_gpu_does_not_get_container_warning(monkeypatch):
    """Do not warn for a native local probe that genuinely has no GPU."""
    monkeypatch.setattr(hardware, "_is_containerized", lambda: False)

    result = {
        "total_ram_gb": 16,
        "available_ram_gb": 10,
        "cpu_cores": 12,
        "cpu_name": "Test CPU",
        "has_gpu": False,
        "gpu_name": None,
        "gpu_vram_gb": None,
        "gpu_count": 0,
        "backend": "cpu_x86",
        "gpu_error": None,
    }

    out = hardware._attach_probe_context(result, host="")

    assert out["containerized"] is False
    assert out["probe_scope"] == "native"
    assert "hardware_visibility_warning" not in out


@pytest.mark.area_services
@pytest.mark.area_unit
def test_remote_probe_does_not_get_local_container_warning(monkeypatch):
    """Do not apply local container warnings to remote hardware probes."""
    monkeypatch.setattr(hardware, "_is_containerized", lambda: True)

    result = {
        "total_ram_gb": 16,
        "available_ram_gb": 10,
        "cpu_cores": 12,
        "cpu_name": "Remote CPU",
        "has_gpu": False,
        "gpu_name": None,
        "gpu_vram_gb": None,
        "gpu_count": 0,
        "backend": "cpu_x86",
        "gpu_error": None,
    }

    out = hardware._attach_probe_context(result, host="user@example.com")

    assert out["containerized"] is False
    assert out["probe_scope"] == "remote"
    assert "hardware_visibility_warning" not in out


@pytest.mark.area_services
@pytest.mark.area_unit
def test_gpu_driver_error_does_not_show_container_no_gpu_warning(monkeypatch):
    """Preserve GPU driver errors instead of replacing them with Docker warnings."""
    monkeypatch.setattr(hardware, "_is_containerized", lambda: True)

    result = {
        "total_ram_gb": 16,
        "available_ram_gb": 10,
        "cpu_cores": 12,
        "cpu_name": "Test CPU",
        "has_gpu": False,
        "gpu_name": None,
        "gpu_vram_gb": None,
        "gpu_count": 0,
        "backend": "cpu_x86",
        "gpu_error": "NVIDIA driver/library version mismatch",
    }

    out = hardware._attach_probe_context(result, host="")

    assert out["containerized"] is True
    assert out["probe_scope"] == "container"
    assert "hardware_visibility_warning" not in out
