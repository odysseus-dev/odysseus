"""Unified-memory NVIDIA detection — Grace Blackwell GB10 / DGX Spark (#1340).

GB10 (and other unified-memory NVIDIA parts) report `nvidia-smi
--query-gpu=memory.total` as "[N/A]"/"Not Supported" because the GPU shares the
system LPDDR pool instead of carrying discrete VRAM. The detector did
`float(memory.total)` and, on the ValueError, `continue`d — dropping the only
GPU row, so a real GB10 running vLLM was reported as "No GPU" and Cookbook
recommendations/model-switching broke. These pin that such a device is detected
as a unified-memory CUDA GPU backed by system RAM, while discrete GPUs are
unchanged.
"""

import pytest

from services.hwfit import hardware


@pytest.fixture(autouse=True)
def _local(monkeypatch):
    monkeypatch.setattr(hardware, "_remote_host", None)


def test_gb10_unified_memory_detected_not_dropped(monkeypatch):
    # Real GB10 nvidia-smi --query-gpu=memory.total,name output: memory is N/A.
    monkeypatch.setattr(hardware, "_run", lambda cmd: "[N/A], NVIDIA GB10")
    monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 128.0)
    info = hardware._detect_nvidia()
    assert info is not None, "GB10 was dropped as 'No GPU'"
    assert info["gpu_name"] == "NVIDIA GB10"
    assert info["backend"] == "cuda"
    assert info["gpu_count"] == 1
    assert info["unified_memory"] is True
    assert info["gpu_vram_gb"] == 128.0          # backed by the unified RAM pool
    assert hardware._last_gpu_error is None


def test_detect_system_reports_gb10_as_gpu(monkeypatch):
    """End-to-end through detect_system: has_gpu True + unified_memory propagated."""
    monkeypatch.setattr(hardware, "_run", lambda cmd: "[N/A], NVIDIA GB10")
    monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 128.0)
    monkeypatch.setattr(hardware, "_get_available_ram_gb", lambda: 120.0)
    monkeypatch.setattr(hardware, "_get_cpu_count", lambda: 20)
    monkeypatch.setattr(hardware, "_get_cpu_name", lambda: "NVIDIA Grace")
    monkeypatch.setattr(hardware, "_detect_apple_silicon", lambda: None)
    s = hardware.detect_system(fresh=True)
    assert s["has_gpu"] is True
    assert s["gpu_name"] == "NVIDIA GB10"
    assert s["backend"] == "cuda"
    assert s.get("unified_memory") is True


def test_discrete_gpu_unchanged_and_not_unified(monkeypatch):
    monkeypatch.setattr(hardware, "_run", lambda cmd: "24576, NVIDIA GeForce RTX 4090")
    info = hardware._detect_nvidia()
    assert info["gpu_vram_gb"] == 24.0
    assert info["gpu_count"] == 1
    assert not info.get("unified_memory")


def test_discrete_takes_precedence_over_unified_row(monkeypatch):
    """A box with a real discrete-VRAM GPU keeps the discrete path; the
    N/A-memory row is not conflated into a unified pool."""
    monkeypatch.setattr(hardware, "_run", lambda cmd: "24576, NVIDIA RTX 4090\n[N/A], NVIDIA GB10")
    info = hardware._detect_nvidia()
    assert info["gpu_name"] == "NVIDIA RTX 4090"
    assert info["gpu_count"] == 1
    assert not info.get("unified_memory")


def test_no_gpu_still_none(monkeypatch):
    """No nvidia-smi output → still None, no spurious unified GPU."""
    monkeypatch.setattr(hardware, "_run", lambda cmd: None)
    assert hardware._detect_nvidia() is None
