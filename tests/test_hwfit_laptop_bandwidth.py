"""Laptop GPU bandwidth must not inherit the desktop model's estimate."""

import pytest

from services.hwfit.fit import _estimate_speed, _lookup_bandwidth


@pytest.mark.parametrize("system", [
    "NVIDIA GeForce RTX 4090 Laptop GPU",
    "nvidia geforce rtx 4090 laptop gpu",
    {"gpu_name": "NVIDIA GeForce RTX 4090 Laptop GPU"},
])
def test_4090_laptop_uses_laptop_memory_bandwidth(system):
    assert _lookup_bandwidth(system) == 576


def test_desktop_4090_keeps_its_bandwidth_and_higher_throughput():
    laptop = {"backend": "cuda", "gpu_name": "NVIDIA GeForce RTX 4090 Laptop GPU"}
    desktop = {"backend": "cuda", "gpu_name": "NVIDIA GeForce RTX 4090"}
    model = {"name": "Example-8B", "parameter_count": "8B"}

    assert _lookup_bandwidth(desktop) == 1008
    laptop_tps = _estimate_speed(model, "Q4_K_M", "gpu", laptop)
    desktop_tps = _estimate_speed(model, "Q4_K_M", "gpu", desktop)
    assert 0 < laptop_tps < desktop_tps
