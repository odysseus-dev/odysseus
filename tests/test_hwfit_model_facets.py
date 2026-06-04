from services.hwfit.fit import analyze_model


def _cuda_system():
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA RTX 4090",
        "gpu_vram_gb": 24.0,
        "gpu_count": 1,
        "available_ram_gb": 64.0,
        "total_ram_gb": 64.0,
    }


def test_gguf_moe_model_exposes_install_facets():
    model = {
        "name": "Qwen/Qwen3-MoE-GGUF",
        "provider": "Qwen",
        "architecture": "qwen3",
        "parameter_count": "7B",
        "parameters_raw": 7_000_000_000,
        "active_parameters": 1_000_000_000,
        "is_moe": True,
        "quantization": "Q4_K_M",
        "context_length": 4096,
        "gguf_sources": [{"repo": "unsloth/Qwen3-MoE-GGUF"}],
    }

    fit = analyze_model(model, _cuda_system())

    assert fit["provider_key"] == "qwen"
    assert fit["provider_family"] == "Qwen"
    assert fit["architecture_family"] == "moe"
    assert fit["architecture_label"] == "MoE"
    assert fit["install_kind"] == "gguf"
    assert fit["recommended_backend"] == "llamacpp"


def test_native_quantized_model_exposes_vllm_facets():
    model = {
        "name": "Example/Looped-FP8",
        "provider": "Example",
        "architecture": "mamba",
        "parameter_count": "3B",
        "parameters_raw": 3_000_000_000,
        "quantization": "FP8",
        "context_length": 4096,
    }

    fit = analyze_model(model, _cuda_system())

    assert fit["architecture_family"] == "looped"
    assert fit["architecture_label"] == "Looped / SSM"
    assert fit["install_kind"] == "safetensors"
    assert fit["recommended_backend"] == "vllm"


def test_provider_filter_label_handles_common_ai_suffixes():
    model = {
        "name": "redhatAI/Granite-MoE-GGUF",
        "provider": "redhatAI",
        "architecture": "granite_moe",
        "parameter_count": "3B",
        "parameters_raw": 3_000_000_000,
        "is_moe": True,
        "quantization": "Q4_K_M",
        "context_length": 4096,
    }

    fit = analyze_model(model, _cuda_system())

    assert fit["provider_key"] == "redhatai"
    assert fit["provider_family"] == "Red Hat AI"


def test_provider_filter_key_canonicalizes_spaced_aliases():
    def fit_for(provider):
        return analyze_model(
            {
                "name": f"{provider}/Mini-GGUF",
                "provider": provider,
                "architecture": "dense",
                "parameter_count": "1B",
                "parameters_raw": 1_000_000_000,
                "quantization": "Q4_K_M",
                "context_length": 4096,
            },
            _cuda_system(),
        )

    spaced = fit_for("Liquid AI")
    compact = fit_for("LiquidAI")

    assert spaced["provider_key"] == compact["provider_key"] == "liquidai"
    assert spaced["provider_family"] == compact["provider_family"] == "Liquid AI"
