"""Tests for _compute_gpu_layers helper in cookbook_helpers.

Covers the three fit modes:
- gpu       → 99 (all layers on GPU)
- cpu_only  → 0
- cpu_offload → proportional layer count (floor(frac * 99))
"""

import pytest
from routes.cookbook_helpers import _compute_gpu_layers


def test_gpu_mode_returns_99():
    assert _compute_gpu_layers("gpu", vram_gb=8.0, required_gb=7.5) == 99


def test_cpu_only_returns_0():
    assert _compute_gpu_layers("cpu_only", vram_gb=None, required_gb=32.0) == 0


def test_cpu_only_with_vram_still_returns_0():
    # run_mode overrides the VRAM hint — if the ranker decided cpu_only, ngl=0.
    assert _compute_gpu_layers("cpu_only", vram_gb=4.0, required_gb=32.0) == 0


def test_cpu_offload_proportional():
    # 8 GB on GPU out of 16 GB required → 50 % → floor(0.5 * 99) = 49
    ngl = _compute_gpu_layers("cpu_offload", vram_gb=8.0, required_gb=16.0)
    assert ngl == 49


def test_cpu_offload_large_fraction():
    # 14 GB on GPU out of 16 GB required → floor(0.875 * 99) = 86
    ngl = _compute_gpu_layers("cpu_offload", vram_gb=14.0, required_gb=16.0)
    assert ngl == 86


def test_cpu_offload_tiny_vram_clamps_to_1():
    # Negligible VRAM — should still return at least 1 (not 0, not negative)
    ngl = _compute_gpu_layers("cpu_offload", vram_gb=0.1, required_gb=32.0)
    assert ngl == 1


def test_missing_vram_falls_back_to_99():
    assert _compute_gpu_layers("cpu_offload", vram_gb=None, required_gb=16.0) == 99


def test_missing_required_falls_back_to_99():
    assert _compute_gpu_layers("cpu_offload", vram_gb=8.0, required_gb=None) == 99


def test_zero_required_falls_back_to_99():
    assert _compute_gpu_layers("cpu_offload", vram_gb=8.0, required_gb=0) == 99


def test_unknown_run_mode_falls_back_to_99():
    assert _compute_gpu_layers("no_fit", vram_gb=None, required_gb=None) == 99
