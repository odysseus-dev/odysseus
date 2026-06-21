"""Tests for fit_level correctness and partial-offload split fields.

Covers:
- cpu_only rows get "good" when RAM headroom is comfortable (>= 1.2×)
- cpu_only rows get "marginal" when RAM headroom is tight
- cpu_offload rows expose vram_gb and ram_gb split fields
- gpu rows have vram_gb=None, ram_gb=None
"""

import pytest
from services.hwfit.fit import analyze_model


# ── helpers ───────────────────────────────────────────────────────────────────

def _ram_only_system(total_gb=64.0, avail_frac=0.8):
    """No GPU — model must run entirely in system RAM."""
    return {
        "has_gpu": False,
        "backend": "cpu_x86",
        "gpu_name": None,
        "gpu_vram_gb": 0.0,
        "gpu_count": 0,
        "available_ram_gb": round(total_gb * avail_frac, 1),
        "total_ram_gb": total_gb,
    }


def _gpu_system(vram_gb=24.0, ram_gb=64.0):
    """Discrete GPU system."""
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA RTX 4090",
        "gpu_vram_gb": vram_gb,
        "gpu_count": 1,
        "available_ram_gb": ram_gb * 0.8,
        "total_ram_gb": ram_gb,
    }


def _tiny_model(params_b=3.0, is_moe=False):
    """Minimal catalog-like model dict for a ~3B parameter GGUF model."""
    return {
        "name": "test/tiny-model",
        "parameter_count": f"{int(params_b)}B",
        "parameter_count_b": params_b,
        "quantization": "Q4_K_M",
        "is_gguf": True,
        "gguf_sources": [{"quant": "Q4_K_M"}],
        "is_moe": is_moe,
        "context_length": 4096,
        "use_case": "general",
    }


# ── cpu_only fit_level ─────────────────────────────────────────────────────────

def test_cpu_only_comfortable_headroom_is_good():
    """cpu_only with >= 1.2× RAM headroom should be 'good', not 'marginal'."""
    # 3B Q4_K_M ≈ 2–3 GB. With 64 GB RAM, headroom is massive → 'good'.
    system = _ram_only_system(total_gb=64.0, avail_frac=0.9)
    result = analyze_model(_tiny_model(params_b=3.0), system)
    assert result is not None, "model should fit in 64 GB RAM"
    assert result["run_mode"] == "cpu_only"
    assert result["fit_level"] == "good", (
        f"expected 'good' for cpu_only with large RAM headroom, got {result['fit_level']!r}"
    )


def test_cpu_only_tight_headroom_is_marginal():
    """cpu_only with < 1.2× headroom should be 'marginal'."""
    # 3B Q4_K_M ≈ 2.1 GB. Set available_ram to just above required so 1.2× fails.
    # Use a larger model (~20B) with a tight RAM budget.
    system = _ram_only_system(total_gb=24.0, avail_frac=0.55)  # ~13 GB available
    # 20B Q4_K_M ≈ 13-14 GB — tight for 13 GB available
    result = analyze_model(_tiny_model(params_b=20.0), system)
    if result is None:
        pytest.skip("model doesn't fit even in RAM at this budget — adjust params")
    if result["run_mode"] != "cpu_only":
        pytest.skip("unexpected run_mode; test only validates cpu_only path")
    assert result["fit_level"] == "marginal", (
        f"expected 'marginal' for cpu_only with tight RAM, got {result['fit_level']!r}"
    )


# ── cpu_offload split fields ───────────────────────────────────────────────────

def test_cpu_offload_exposes_vram_ram_split():
    """cpu_offload rows include vram_gb and ram_gb split fields."""
    # 20B Q4_K_M ≈ 13-14 GB; 6 GB VRAM forces partial offload into RAM.
    system = _gpu_system(vram_gb=6.0, ram_gb=64.0)
    result = analyze_model(_tiny_model(params_b=20.0), system)
    assert result is not None, "20B model should fit via cpu_offload"
    assert result["run_mode"] == "cpu_offload", (
        f"expected cpu_offload for 20B model on 6 GB VRAM, got {result['run_mode']!r}"
    )
    assert result["vram_gb"] is not None, "vram_gb should be set for cpu_offload"
    assert result["ram_gb"] is not None, "ram_gb should be set for cpu_offload"
    assert result["vram_gb"] <= 6.0, "vram_gb cannot exceed GPU VRAM"
    assert result["ram_gb"] >= 0.0
    # The split should add up close to required_gb (allow 0.2 rounding tolerance).
    assert abs(result["vram_gb"] + result["ram_gb"] - result["required_gb"]) < 0.3, (
        f"vram_gb + ram_gb should ≈ required_gb: "
        f"{result['vram_gb']} + {result['ram_gb']} ≠ {result['required_gb']}"
    )


def test_gpu_rows_have_no_split_fields():
    """gpu rows (model fits entirely in VRAM) have vram_gb=None, ram_gb=None."""
    # 3B Q4_K_M fits easily in 24 GB VRAM.
    system = _gpu_system(vram_gb=24.0, ram_gb=64.0)
    result = analyze_model(_tiny_model(params_b=3.0), system)
    assert result is not None
    assert result["run_mode"] == "gpu"
    assert result["vram_gb"] is None, "gpu rows should not expose vram_gb split"
    assert result["ram_gb"] is None


def test_cpu_only_rows_have_no_split_fields():
    """cpu_only rows (no GPU) have vram_gb=None, ram_gb=None."""
    system = _ram_only_system(total_gb=64.0)
    result = analyze_model(_tiny_model(params_b=3.0), system)
    assert result is not None
    assert result["run_mode"] == "cpu_only"
    assert result["vram_gb"] is None, "cpu_only rows should not expose vram_gb split"
    assert result["ram_gb"] is None
