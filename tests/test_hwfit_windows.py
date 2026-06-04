"""Windows support for Cookbook hardware-fit.

On Windows the only serving path is llama.cpp/Ollama (GGUF): vLLM and SGLang are
blocked there (routes/cookbook_routes.py emits "vLLM is not supported on
Windows"). So even on an NVIDIA/CUDA Windows box, AWQ/GPTQ/FP8 safetensors
models with no GGUF alternate can be downloaded but never served. These tests
lock in that Windows is treated GGUF-only like Apple Silicon and consumer AMD
RDNA, while Linux/CUDA — where vLLM serves safetensors — stays untouched.

Regression for issue #2526.
"""

import json

from services.hwfit import hardware
from services.hwfit.fit import rank_models
from services.hwfit.models import get_models


def _windows_cuda_system(ram_gb=32.0, vram_gb=8.0):
    """An NVIDIA Windows box, as _detect_windows() reports it: CUDA backend, but
    with the platform flag that marks the host as Windows."""
    return {
        "has_gpu": True,
        "backend": "cuda",
        "platform": "windows",
        "gpu_name": "NVIDIA GeForce RTX 4060",
        "gpu_vram_gb": vram_gb,
        "gpu_count": 1,
        "available_ram_gb": ram_gb * 0.7,
        "total_ram_gb": ram_gb,
    }


def _linux_cuda_system():
    """Same CUDA hardware on Linux — no platform flag — where vLLM serves
    safetensors directly."""
    return {
        "has_gpu": True, "backend": "cuda", "gpu_name": "NVIDIA RTX 4090",
        "gpu_vram_gb": 24.0, "gpu_count": 1, "available_ram_gb": 32.0, "total_ram_gb": 64.0,
    }


def test_only_gguf_models_recommended_on_windows():
    """llama.cpp/Ollama (the only Windows-servable engines) need GGUF, so every
    model recommended on Windows must ship a real GGUF — no vLLM-only AWQ/GPTQ/
    FP8 safetensors that the user could download but never launch."""
    catalog = {m["name"]: m for m in get_models()}
    unservable = [
        r["name"] for r in rank_models(_windows_cuda_system(), limit=900)
        if not (catalog.get(r["name"], {}).get("is_gguf")
                or catalog.get(r["name"], {}).get("gguf_sources"))
    ]
    assert unservable == [], f"{len(unservable)} non-GGUF models on Windows, e.g. {unservable[:3]}"


def test_prequantized_safetensors_hidden_on_windows():
    """A safetensors-only AWQ/GPTQ/FP8 model with no GGUF alternate must not be
    recommended on Windows — that is the exact failure mode from #2526."""
    catalog = {m["name"]: m for m in get_models()}
    names = {r["name"] for r in rank_models(_windows_cuda_system(vram_gb=24.0), limit=900)}
    leaked = [
        n for n in names
        if not (catalog.get(n, {}).get("is_gguf") or catalog.get(n, {}).get("gguf_sources"))
    ]
    assert leaked == [], f"unservable safetensors recommended on Windows: {leaked[:3]}"


def test_safetensors_models_still_recommended_on_linux_cuda():
    """Regression guard: the GGUF-only rule is Windows-specific. On Linux/CUDA,
    vLLM serves safetensors, so non-GGUF repos must still be recommended."""
    names = {r["name"] for r in rank_models(_linux_cuda_system(), limit=900)}
    assert "microsoft/Phi-mini-MoE-instruct" in names


def test_gguf_models_still_recommended_on_windows():
    """The filter only hides non-GGUF models — real GGUF repos must still rank on
    Windows so the Cookbook isn't left empty."""
    catalog = {m["name"]: m for m in get_models()}
    names = {r["name"] for r in rank_models(_windows_cuda_system(vram_gb=24.0), limit=900)}
    gguf_recommended = [
        n for n in names
        if catalog.get(n, {}).get("is_gguf") or catalog.get(n, {}).get("gguf_sources")
    ]
    assert gguf_recommended, "no GGUF models recommended on Windows — filter is too aggressive"


def test_detect_windows_sets_platform(monkeypatch):
    """_detect_windows() must stamp platform='windows' on its result so
    rank_models can apply the GGUF-only rule on an otherwise CUDA-looking box."""
    probe = json.dumps({
        "ram_gb": 32.0, "avail_gb": 24.0, "cpu_cores": 16, "cpu_name": "Intel Core i7",
        "arch": 64, "gpu_name": "NVIDIA GeForce RTX 4060", "gpu_vram_gb": 8.0,
        "gpu_count": 1, "gpu_backend": "cuda",
    })
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware, "_powershell_exe", lambda: "powershell")
    monkeypatch.setattr(hardware, "_run", lambda *a, **k: probe)

    result = hardware._detect_windows()
    assert result is not None
    assert result["platform"] == "windows"
    assert result["backend"] == "cuda"
    assert result["has_gpu"] is True
