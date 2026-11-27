import pytest
from services.hwfit.fit import (
    analyze_model,
    rank_models,
    _infer_model_modalities,
    _matches_multimodal_requirement,
    _resolve_backend_support,
    _reliability_score,
)


CUDA_SYSTEM = {
    "has_gpu": True,
    "gpu_name": "NVIDIA RTX 4090",
    "gpu_vram_gb": 24.0,
    "gpu_count": 1,
    "available_ram_gb": 64.0,
    "total_ram_gb": 64.0,
    "backend": "cuda",
    "platform": "linux",
}

METAL_SYSTEM = {
    "has_gpu": True,
    "gpu_name": "Apple M4 Max",
    "gpu_vram_gb": 48.0,
    "gpu_count": 1,
    "available_ram_gb": 64.0,
    "total_ram_gb": 64.0,
    "backend": "metal",
    "unified_memory": True,
    "platform": "darwin",
}


def test_multimodal_inference_and_filtering(monkeypatch):
    vision_model = {
        "name": "Qwen/Qwen2.5-VL-7B-Instruct",
        "provider": "Qwen",
        "architecture": "qwen2_5_vl",
        "parameter_count": "7B",
        "context_length": 32768,
        "gguf_sources": ["bartowski/Qwen2.5-VL-7B-Instruct-GGUF"],
    }
    audio_model = {
        "name": "Qwen/Qwen2.5-Omni-7B",
        "provider": "Qwen",
        "architecture": "qwen2_5_omni",
        "modalities": ["text", "image", "audio"],
        "parameter_count": "7B",
        "context_length": 32768,
        "gguf_sources": ["bartowski/Qwen2.5-Omni-7B-GGUF"],
    }
    text_model = {
        "name": "Qwen/Qwen3-8B",
        "provider": "Qwen",
        "architecture": "qwen3",
        "parameter_count": "8B",
        "context_length": 32768,
        "gguf_sources": ["bartowski/Qwen3-8B-GGUF"],
    }

    assert _infer_model_modalities(vision_model) == ["text", "image"]
    assert _infer_model_modalities(audio_model) == ["text", "image", "audio", "video"]
    assert _infer_model_modalities(text_model) == ["text"]
    assert _matches_multimodal_requirement(["text", "image"], "vision") is True
    assert _matches_multimodal_requirement(["text"], "vision") is False
    assert _matches_multimodal_requirement(["text", "image", "audio"], "audio") is True

    monkeypatch.setattr("services.hwfit.fit.get_models", lambda: [vision_model, audio_model, text_model])

    vision_ranked = rank_models(CUDA_SYSTEM, multimodal="vision")
    assert {m["name"] for m in vision_ranked} == {
        "Qwen/Qwen2.5-Omni-7B",
        "Qwen/Qwen2.5-VL-7B-Instruct",
    }
    assert all(m["supports_vision"] is True for m in vision_ranked)

    audio_ranked = rank_models(CUDA_SYSTEM, multimodal="audio")
    assert [m["name"] for m in audio_ranked] == ["Qwen/Qwen2.5-Omni-7B"]
    assert audio_ranked[0]["supports_audio"] is True


def test_backend_support_and_filtering_across_cuda_and_metal(monkeypatch):
    gguf_model = {
        "name": "meta-llama/Llama-3.3-8B-Instruct",
        "provider": "Meta",
        "architecture": "llama",
        "parameter_count": "8B",
        "context_length": 131072,
        "gguf_sources": ["bartowski/Llama-3.3-8B-Instruct-GGUF"],
    }
    awq_model = {
        "name": "Qwen/Qwen3-14B-AWQ",
        "provider": "Qwen",
        "architecture": "qwen3",
        "quantization": "AWQ-4bit",
        "parameter_count": "14B",
        "context_length": 32768,
    }
    mlx_model = {
        "name": "mlx-community/Qwen3-8B-4bit",
        "provider": "mlx-community",
        "architecture": "qwen3",
        "quantization": "mlx-4bit",
        "parameter_count": "8B",
        "context_length": 32768,
    }

    monkeypatch.setattr("services.hwfit.fit.get_models", lambda: [gguf_model, awq_model, mlx_model])

    cuda_vllm = rank_models(CUDA_SYSTEM, backend="vllm")
    assert {m["name"] for m in cuda_vllm} == {
        "meta-llama/Llama-3.3-8B-Instruct",
        "Qwen/Qwen3-14B-AWQ",
    }
    for row in cuda_vllm:
        assert "vllm" in row["supported_backends"]
        assert "sglang" in row["supported_backends"]

    metal_mlx = rank_models(METAL_SYSTEM, backend="mlx")
    assert [m["name"] for m in metal_mlx] == ["mlx-community/Qwen3-8B-4bit"]
    assert metal_mlx[0]["recommended_backend"] == "mlx"


def test_reliability_architecture_and_quantization_scores_and_sorts(monkeypatch):
    official_modern = {
        "name": "Qwen/Qwen3.6-8B",
        "provider": "Qwen",
        "architecture": "qwen3_6",
        "parameter_count": "8B",
        "context_length": 32768,
        "downloads": 250_000,
        "gguf_sources": ["Qwen/Qwen3.6-8B-GGUF"],
    }
    unverified_older = {
        "name": "random-user/older-llama2-7b",
        "provider": "random-user",
        "architecture": "llama",
        "parameter_count": "7B",
        "context_length": 4096,
        "downloads": 12,
    }

    r_official = analyze_model(official_modern, CUDA_SYSTEM, target_quant="Q8_0")
    r_unverified = analyze_model(unverified_older, CUDA_SYSTEM, target_quant="Q2_K")

    assert r_official["reliability_tier"] == "high"
    assert r_official["scores"]["reliability"] > r_unverified["scores"]["reliability"]
    assert r_official["scores"]["architecture"] > r_unverified["scores"]["architecture"]
    assert r_official["scores"]["quantization"] > r_unverified["scores"]["quantization"]
    assert r_official["score"] > r_unverified["score"]

    monkeypatch.setattr("services.hwfit.fit.get_models", lambda: [unverified_older, official_modern])
    by_rel = rank_models(CUDA_SYSTEM, sort="reliability")
    assert by_rel[0]["name"] == "Qwen/Qwen3.6-8B"

    by_arch = rank_models(CUDA_SYSTEM, sort="architecture")
    assert by_arch[0]["name"] == "Qwen/Qwen3.6-8B"
