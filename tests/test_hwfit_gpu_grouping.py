"""NVIDIA GPU grouping + driver-error surfacing in hwfit detection.

Two behaviours the Cookbook depends on:

  * `_group_gpus` splits a box into homogeneous pools (vLLM tensor-parallel only
    works across identical cards), biggest pool first, each carrying its CUDA
    device indices so a serve command can pin CUDA_VISIBLE_DEVICES.
  * `_detect_nvidia` must tell "nvidia-smi present but the driver is broken"
    (e.g. a driver/library mismatch after an update-without-reboot) apart from
    "no GPU", surfacing the former as a gpu_error instead of the misleading
    "No GPU".
"""

import pytest

from services.hwfit import hardware


# ── _group_gpus ──────────────────────────────────────────────────────────────


def test_group_gpus_collapses_identical_cards():
    gpus = [
        {"index": 0, "name": "NVIDIA GeForce RTX 4090", "vram_gb": 24.0},
        {"index": 1, "name": "NVIDIA GeForce RTX 4090", "vram_gb": 24.0},
    ]
    groups = hardware._group_gpus(gpus)
    assert len(groups) == 1
    g = groups[0]
    assert g["count"] == 2
    assert g["vram_each"] == 24.0
    assert g["vram_total"] == 48.0
    assert g["indices"] == [0, 1]


def test_group_gpus_splits_mixed_box_biggest_pool_first():
    gpus = [
        {"index": 0, "name": "NVIDIA GeForce RTX 4090", "vram_gb": 24.0},
        {"index": 1, "name": "NVIDIA GeForce RTX 4090", "vram_gb": 24.0},
        {"index": 2, "name": "NVIDIA A100", "vram_gb": 80.0},
    ]
    groups = hardware._group_gpus(gpus)
    assert len(groups) == 2
    # A100 (80 GB) outranks the 4090 pair (48 GB total) → listed first.
    assert groups[0]["name"] == "NVIDIA A100"
    assert groups[0]["count"] == 1
    assert groups[0]["indices"] == [2]
    assert groups[1]["name"] == "NVIDIA GeForce RTX 4090"
    assert groups[1]["count"] == 2
    # Indices are preserved so a serve command can target exactly this pool.
    assert groups[1]["indices"] == [0, 1]


def test_group_gpus_keys_on_name_and_vram():
    """Same model name but different VRAM (e.g. a 16 GB vs 24 GB variant) must
    not be merged — vLLM can't tensor-parallel across unequal cards."""
    gpus = [
        {"index": 0, "name": "Tesla T4", "vram_gb": 16.0},
        {"index": 1, "name": "Tesla T4", "vram_gb": 24.0},
    ]
    groups = hardware._group_gpus(gpus)
    assert len(groups) == 2


# ── _detect_nvidia ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _local_no_remote(monkeypatch):
    """Keep detection on the local path so the SSH fallbacks never fire."""
    monkeypatch.setattr(hardware, "_remote_host", None)


def test_detect_nvidia_parses_homogeneous_box(monkeypatch):
    monkeypatch.setattr(
        hardware, "_run",
        lambda cmd: "24576, NVIDIA GeForce RTX 4090\n24576, NVIDIA GeForce RTX 4090",
    )
    info = hardware._detect_nvidia()
    assert info is not None
    assert info["backend"] == "cuda"
    assert info["gpu_count"] == 2
    assert info["gpu_vram_gb"] == 48.0
    assert info["homogeneous"] is True
    assert len(info["gpu_groups"]) == 1
    # A clean detection leaves no driver-error flag behind.
    assert hardware._last_gpu_error is None


def test_detect_nvidia_reports_mixed_box_as_non_homogeneous(monkeypatch):
    monkeypatch.setattr(
        hardware, "_run",
        lambda cmd: (
            "81920, NVIDIA A100\n"
            "24576, NVIDIA GeForce RTX 4090\n"
            "24576, NVIDIA GeForce RTX 4090"
        ),
    )
    info = hardware._detect_nvidia()
    assert info["gpu_count"] == 3
    assert info["homogeneous"] is False
    assert len(info["gpu_groups"]) == 2
    assert info["gpu_groups"][0]["name"] == "NVIDIA A100"  # biggest pool first


def test_detect_nvidia_surfaces_driver_mismatch(monkeypatch):
    """nvidia-smi present but unable to talk to the driver → gpu_error, not
    a silent "No GPU"."""
    monkeypatch.setattr(
        hardware, "_run",
        lambda cmd: "Failed to initialize NVML: Driver/library version mismatch",
    )
    info = hardware._detect_nvidia()
    assert info is None
    assert hardware._last_gpu_error is not None
    assert "mismatch" in hardware._last_gpu_error.lower()


def test_detect_nvidia_no_smi_is_plain_no_gpu(monkeypatch):
    """No nvidia-smi at all (returns nothing) → no GPU, and crucially no
    spurious driver-error flag."""
    monkeypatch.setattr(hardware, "_run", lambda cmd: None)
    info = hardware._detect_nvidia()
    assert info is None
    assert hardware._last_gpu_error is None
