"""Windows support for Cookbook hardware-fit.

Odysseus only supports llama.cpp on Windows (vLLM/SGLang are explicitly
blocked). llama.cpp requires GGUF, so non-GGUF models — including AWQ/GPTQ/
FP8 safetensors repos — must be filtered out on Windows so the Cookbook does
not recommend models the user cannot actually serve.
"""

from services.hwfit.fit import rank_models
from services.hwfit.models import get_models


def _windows_system(ram_gb=32.0, vram_gb=16.0):
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA RTX 4060",
        "gpu_vram_gb": vram_gb,
        "gpu_count": 1,
        "available_ram_gb": ram_gb * 0.7,
        "total_ram_gb": ram_gb,
        "platform": "windows",
    }


def _cuda_system():
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA RTX 4090",
        "gpu_vram_gb": 24.0,
        "gpu_count": 1,
        "available_ram_gb": 32.0,
        "total_ram_gb": 64.0,
    }


def test_only_gguf_models_recommended_on_windows():
    """llama.cpp (GGUF) is the only servable path on Windows, so every model
    recommended there must ship a real GGUF — no vLLM-only AWQ/GPTQ/FP8."""
    catalog = {m["name"]: m for m in get_models()}
    unservable = [
        r["name"] for r in rank_models(_windows_system(), limit=900)
        if not (catalog.get(r["name"], {}).get("is_gguf")
                or catalog.get(r["name"], {}).get("gguf_sources"))
    ]
    assert unservable == [], f"{len(unservable)} non-GGUF models on Windows, e.g. {unservable[:3]}"


def test_safetensors_models_still_recommended_on_cuda():
    """Regression guard: the GGUF-only rule must not leak onto CUDA."""
    names = {r["name"] for r in rank_models(_cuda_system(), limit=900)}
    assert "microsoft/Phi-mini-MoE-instruct" in names


def test_awq_model_hidden_on_windows():
    """The user's reported issue: Qwen2.5-3B-Instruct-AWQ is AWQ-only and must
    not be recommended on Windows where it cannot be served."""
    names = {r["name"] for r in rank_models(_windows_system(), limit=900)}
    assert "Qwen/Qwen2.5-3B-Instruct-AWQ" not in names


def test_awq_model_visible_on_cuda():
    """The same AWQ model should still be visible on CUDA where vLLM can
    serve it."""
    names = {r["name"] for r in rank_models(_cuda_system(), limit=900)}
    assert "Qwen/Qwen2.5-3B-Instruct-AWQ" in names


def test_gguf_alternate_still_recommended_on_windows():
    """Qwen2.5-3B-Instruct (the base model) has a GGUF source, so it should
    still appear on Windows even though the AWQ variant is hidden."""
    names = {r["name"] for r in rank_models(_windows_system(), limit=900)}
    assert "Qwen/Qwen2.5-3B-Instruct" in names
