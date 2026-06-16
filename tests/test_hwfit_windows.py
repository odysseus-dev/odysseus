"""Windows support for Cookbook hardware-fit.

Odysseus only supports llama.cpp on Windows (vLLM/SGLang are explicitly
blocked). llama.cpp requires GGUF, so non-GGUF models — including AWQ/GPTQ/
FP8 safetensors repos — must be filtered out on Windows so the Cookbook does
not recommend models the user cannot actually serve.
"""

import json

from services.hwfit import hardware
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


def test_detect_windows_parses_nvidia_payload(monkeypatch):
    """_detect_windows should preserve CUDA backend and split aggregate VRAM
    evenly across reported device count when nvidia-smi data is present."""
    payload = {
        "ram_gb": 64.0,
        "avail_gb": 40.0,
        "cpu_name": "AMD Ryzen 9 9950X",
        "cpu_cores": 32.0,
        "gpu_name": "NVIDIA RTX 5090",
        "gpu_vram_gb": 48.0,
        "gpu_count": 2.0,
        "gpu_backend": "cuda",
    }
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware, "_run", lambda _cmd: json.dumps(payload))

    info = hardware._detect_windows()
    assert info is not None
    assert info["backend"] == "cuda"
    assert info["gpu_count"] == 2
    assert info["gpu_vram_gb"] == 48.0
    assert info["rdna_gen"] == 2
    assert len(info["gpus"]) == 2
    assert info["gpus"][0]["vram_gb"] == 24.0
    assert info["gpu_groups"][0]["count"] == 2


def test_detect_windows_parses_amd_payload_with_rdna3(monkeypatch):
    """AMD ROCm payload should carry through rdna_gen=3 for RDNA3/4 cards."""
    payload = {
        "ram_gb": 32.0,
        "avail_gb": 20.0,
        "cpu_name": "AMD Ryzen 7 9700X",
        "cpu_cores": 16,
        "gpu_name": "AMD Radeon RX 9070 XT",
        "gpu_vram_gb": 16.0,
        "gpu_count": 1,
        "gpu_backend": "rocm",
        "rdna_gen": 3,
    }
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware, "_run", lambda _cmd: json.dumps(payload))

    info = hardware._detect_windows()
    assert info is not None
    assert info["backend"] == "rocm"
    assert info["gpu_name"] == "AMD Radeon RX 9070 XT"
    assert info["gpu_vram_gb"] == 16.0
    assert info["rdna_gen"] == 3


def test_detect_windows_non_amd_payload_defaults_rdna(monkeypatch):
    """Non-AMD payloads should keep rdna_gen safely defaulted to 2."""
    payload = {
        "ram_gb": 32.0,
        "avail_gb": 18.0,
        "cpu_name": "Intel Core Ultra 9",
        "cpu_cores": 16,
        "gpu_name": "Intel Arc B580",
        "gpu_vram_gb": 12.0,
        "gpu_count": 1,
        "gpu_backend": "cpu_x86",
    }
    monkeypatch.setattr(hardware, "_remote_host", None)
    monkeypatch.setattr(hardware, "_run", lambda _cmd: json.dumps(payload))

    info = hardware._detect_windows()
    assert info is not None
    assert info["backend"] == "cpu_x86"
    assert info["rdna_gen"] == 2
