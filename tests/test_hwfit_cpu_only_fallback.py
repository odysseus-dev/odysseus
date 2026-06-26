"""Regression test for cpu_only backend fallback in hwfit speed estimation."""

import pytest

from services.hwfit.fit import _estimate_speed


DENSE_MODEL = {
    "name": "Test-7B",
    "parameter_count": "7B",
    "parameters_raw": 7_000_000_000,
}

CUDA_SYSTEM = {
    "backend": "cuda",
    "gpu_name": "NVIDIA RTX 4090",
    "gpu_vram_gb": 24.0,
}

CPU_X86_SYSTEM = {
    "backend": "cpu_x86",
    "gpu_name": None,
    "gpu_vram_gb": 0,
}

CPU_ARM_SYSTEM = {
    "backend": "cpu_arm",
    "gpu_name": None,
    "gpu_vram_gb": 0,
}

METAL_SYSTEM = {
    "backend": "metal",
    "gpu_name": "Apple M3 Max",
    "gpu_vram_gb": 36.0,
}

ROCM_SYSTEM = {
    "backend": "rocm",
    "gpu_name": "AMD Radeon RX 7900 XTX",
    "gpu_vram_gb": 24.0,
}

ARM64_SYSTEM = {
    "backend": "arm64",
    "gpu_name": None,
    "gpu_vram_gb": 0,
}

ARM32_SYSTEM = {
    "backend": "arm",
    "gpu_name": None,
    "gpu_vram_gb": 0,
}

AARCH64_SYSTEM = {
    "backend": "aarch64",
    "gpu_name": None,
    "gpu_vram_gb": 0,
}

QUANT = "Q4_K_M"


@pytest.mark.parametrize(
    "non_cpu_system",
    [CUDA_SYSTEM, ROCM_SYSTEM],
    ids=["cuda", "rocm"],
)
def test_cpu_only_on_non_cpu_backend_uses_cpu_x86_fallback(non_cpu_system):
    """cpu_only must ignore discrete GPU backends and use the x86 CPU fallback constant."""
    non_cpu_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", non_cpu_system)
    cpu_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", CPU_X86_SYSTEM)

    assert non_cpu_tps == pytest.approx(cpu_tps, rel=1e-9, abs=1e-9)
    assert non_cpu_tps > 0


def test_cpu_only_on_metal_apple_silicon_uses_cpu_arm_fallback():
    """Apple Silicon/Metal cpu_only should map to the ARM CPU fallback constant."""
    metal_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", METAL_SYSTEM)
    arm_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", CPU_ARM_SYSTEM)

    assert metal_tps == pytest.approx(arm_tps, rel=1e-9, abs=1e-9)
    assert metal_tps > 0


def test_cpu_only_on_gpu_backend_uses_detected_arm64_cpu_arch():
    """A GPU backend on an ARM64 host should use the ARM CPU fallback for cpu_only."""
    cuda_arm64 = dict(CUDA_SYSTEM, cpu_arch="aarch64", cpu_name="Ampere Altra")
    cuda_arm64_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", cuda_arm64)
    arm_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", CPU_ARM_SYSTEM)

    assert cuda_arm64_tps == pytest.approx(arm_tps, rel=1e-9, abs=1e-9)
    assert cuda_arm64_tps > 0


@pytest.mark.parametrize(
    "arm_alias_system",
    [ARM64_SYSTEM, AARCH64_SYSTEM, CPU_ARM_SYSTEM],
    ids=["arm64", "aarch64", "cpu_arm"],
)
def test_cpu_only_preserves_arm_backends(arm_alias_system):
    """ARM CPU backends and their aliases must stay on the ARM CPU fallback."""
    alias_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", arm_alias_system)
    arm_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", CPU_ARM_SYSTEM)

    assert alias_tps == pytest.approx(arm_tps, rel=1e-9, abs=1e-9)
    assert alias_tps > 0


def test_cpu_only_does_not_treat_plain_arm_as_arm64_fallback():
    """Docker/OCI plain arm is not the ARM64-class fallback used for Apple Silicon."""
    arm32_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", ARM32_SYSTEM)
    x86_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", CPU_X86_SYSTEM)

    assert arm32_tps == pytest.approx(x86_tps, rel=1e-9, abs=1e-9)
    assert arm32_tps > 0


def test_cpu_only_preserves_known_cpu_backends():
    """Known CPU backends should be preserved, not rewritten to cpu_x86."""
    for system in (CPU_X86_SYSTEM, CPU_ARM_SYSTEM):
        tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", system)
        assert tps > 0

    # The two CPU backends use different fallback constants, so their results
    # must differ (cpu_arm is faster in the fallback table than cpu_x86).
    x86_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", CPU_X86_SYSTEM)
    arm_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", CPU_ARM_SYSTEM)
    assert arm_tps != x86_tps
    assert arm_tps > x86_tps


def test_cpu_only_on_cuda_is_slower_than_gpu_path():
    """The CPU-only estimate on a CUDA system must not exceed the GPU path."""
    cpu_only_tps = _estimate_speed(DENSE_MODEL, QUANT, "cpu_only", CUDA_SYSTEM)
    gpu_tps = _estimate_speed(DENSE_MODEL, QUANT, "gpu", CUDA_SYSTEM)

    assert cpu_only_tps < gpu_tps
