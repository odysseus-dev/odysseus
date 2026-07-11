"""macOS / Apple Silicon (Metal) support for Cookbook hardware-fit.

Covers the Metal-specific behavior added for Apple Silicon and locks in the
guarantee that non-macOS (Linux/Windows) detection is unchanged.
"""

import json

from services.hwfit import hardware
from services.hwfit.fit import rank_models
from services.hwfit.models import get_models


def _metal_system(ram_gb=16.0, vram_gb=10.7):
    return {
        "has_gpu": True,
        "backend": "metal",
        "gpu_name": "Apple M2",
        "gpu_vram_gb": vram_gb,
        "gpu_count": 1,
        "available_ram_gb": ram_gb * 0.7,
        "total_ram_gb": ram_gb,
        "unified_memory": True,
    }


def _fake_sysctl(brand="Apple M2 Pro", memsize_gb=32, wired_mb=None, display_json=None, display_text=None):
    def run(cmd):
        joined = " ".join(cmd)
        if "machdep.cpu.brand_string" in joined:
            return brand
        if "hw.memsize" in joined:
            return str(int(memsize_gb * 1024**3))
        if "iogpu.wired_limit_mb" in joined:
            return str(wired_mb) if wired_mb is not None else None
        if "system_profiler SPDisplaysDataType -json" in joined:
            if isinstance(display_json, (dict, list)):
                return json.dumps(display_json)
            return display_json
        if "system_profiler SPDisplaysDataType" in joined:
            return display_text
        return None
    return run


def test_mlx_models_visible_on_metal():
    """Apple Silicon can serve MLX models through the MLX engine, so Cookbook
    should surface MLX rows on Metal while still hiding them on CUDA."""
    results = rank_models(_metal_system(), limit=900)
    mlx = [m for m in results if str(m.get("quant", "")).startswith("mlx-")]
    assert mlx, "MLX models should be available on Apple Silicon / Metal hardware"


def _cuda_system():
    return {
        "has_gpu": True, "backend": "cuda", "gpu_name": "NVIDIA RTX 4090",
        "gpu_vram_gb": 24.0, "gpu_count": 1, "available_ram_gb": 32.0, "total_ram_gb": 64.0,
    }


def test_mlx_hidden_on_cuda_backend_unchanged():
    """Regression guard: Linux/CUDA users never saw MLX before and still don't."""
    mlx = [m for m in rank_models(_cuda_system(), limit=900) if str(m.get("quant", "")).startswith("mlx-")]
    assert mlx == []


def test_only_gguf_or_mlx_models_recommended_on_metal():
    """Metal recommendations must be servable locally: either GGUF for
    llama.cpp/Ollama or MLX for the MLX engine."""
    catalog = {m["name"]: m for m in get_models()}
    unservable = [
        r["name"] for r in rank_models(_metal_system(), limit=900)
        if not (str(r.get("quant", "")).startswith("mlx-")
                or str(r.get("name", "")).startswith(("mlx-community/", "lmstudio-community/"))
                or catalog.get(r["name"], {}).get("is_gguf")
                or catalog.get(r["name"], {}).get("gguf_sources"))
    ]
    assert unservable == [], f"{len(unservable)} non-servable models on Metal, e.g. {unservable[:3]}"


def test_qwen_catalog_entries_point_at_verified_gguf_repos():
    """Qwen GGUF-looking Cookbook rows must download GGUF repos, not the base
    safetensors repositories."""
    catalog = {m["name"]: m for m in get_models()}
    expected = {
        "Qwen/Qwen3.5-9B": ("unsloth/Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf"),
        "Qwen/Qwen3.6-27B": ("unsloth/Qwen3.6-27B-GGUF", "Qwen3.6-27B-Q4_K_M.gguf"),
        "Qwen/Qwen3.6-35B-A3B": ("unsloth/Qwen3.6-35B-A3B-GGUF", "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"),
    }

    for model_name, (repo, filename) in expected.items():
        sources = catalog[model_name].get("gguf_sources") or []
        assert any(src.get("repo") == repo and src.get("file") == filename for src in sources)


def test_safetensors_models_still_recommended_on_cuda():
    """Regression guard: vLLM serves safetensors on CUDA, so non-GGUF repos must
    NOT be filtered there — the GGUF-only rule is Metal-specific."""
    names = {r["name"] for r in rank_models(_cuda_system(), limit=900)}
    assert "microsoft/Phi-mini-MoE-instruct" in names


def test_apple_silicon_detected_as_metal(monkeypatch):
    """On local Apple Silicon, detection reports a Metal GPU with a RAM-scaled
    unified-memory budget."""
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(hardware, "_run", _fake_sysctl(
        memsize_gb=32,
        display_json={"SPDisplaysDataType": [{"sppci_model": "Apple M2 Pro", "sppci_cores": "19"}]},
    ))

    info = hardware._detect_apple_silicon()
    assert info is not None
    assert info["backend"] == "metal"
    assert info["gpu_name"] == "Apple M2 Pro"
    assert info["unified_memory"] is True
    assert info["gpu_cores"] == 19
    assert info["gpu_vram_gb"] == 24.0  # 32GB * 0.75


def test_apple_silicon_gpu_cores_fall_back_to_plain_text(monkeypatch):
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(hardware, "_run", _fake_sysctl(
        brand="Apple M4 Max",
        memsize_gb=64,
        display_json="{not-json",
        display_text="Graphics/Displays:\n\nApple M4 Max:\n  Total Number of Cores: 32\n",
    ))

    info = hardware._detect_apple_silicon()
    assert info is not None
    assert info["gpu_cores"] == 32


def test_apple_silicon_gpu_cores_are_optional(monkeypatch):
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(hardware, "_run", _fake_sysctl(memsize_gb=32))

    info = hardware._detect_apple_silicon()
    assert info is not None
    assert "gpu_cores" not in info


def test_apple_silicon_skipped_on_linux(monkeypatch):
    """Guarantee Linux detection is untouched: the Metal probe bails immediately."""
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(hardware, "_run", _fake_sysctl())
    assert hardware._detect_apple_silicon() is None


def test_intel_mac_skipped(monkeypatch):
    """Intel Macs have no Metal GPU worth serving LLMs on — fall through to CPU."""
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(hardware, "_run", _fake_sysctl())
    assert hardware._detect_apple_silicon() is None


def test_plain_arm_mac_skipped(monkeypatch):
    """Only ARM64-class Macs should enter the Apple Silicon Metal path."""
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hardware.platform, "machine", lambda: "armv7l")
    monkeypatch.setattr(hardware, "_run", _fake_sysctl())
    assert hardware._detect_apple_silicon() is None


def test_detect_system_propagates_unified_memory(monkeypatch):
    """The unified_memory flag set by GPU detection must survive into the
    system dict so the API and UI can report it (it was being dropped)."""
    monkeypatch.setattr(hardware, "_detect_apple_silicon", lambda: {
        "gpu_name": "Apple M4", "gpu_vram_gb": 10.7, "gpu_count": 1,
        "gpus": [], "gpu_groups": [], "homogeneous": True,
        "backend": "metal", "unified_memory": True, "gpu_cores": 10,
    })
    monkeypatch.setattr(hardware, "_get_ram_gb", lambda: 16.0)
    monkeypatch.setattr(hardware, "_get_available_ram_gb", lambda: 11.0)
    monkeypatch.setattr(hardware, "_get_cpu_count", lambda: 10)
    monkeypatch.setattr(hardware, "_get_cpu_name", lambda: "Apple M4")

    s = hardware.detect_system(fresh=True)
    assert s["backend"] == "metal"
    assert s.get("unified_memory") is True
    assert s["gpu_cores"] == 10
