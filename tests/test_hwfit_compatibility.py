import pytest
from services.hwfit.models import get_model_format, is_format_compatible
from services.hwfit.fit import rank_models

def test_get_model_format():
    model_gguf = {"name": "Llama-3", "gguf_sources": [{"repo": "unsloth/Llama-3-GGUF"}]}
    model_awq = {"name": "Qwen-AWQ", "quantization": "AWQ-4bit"}
    model_gptq = {"name": "Llama-GPTQ", "quantization": "GPTQ-Int4"}
    model_fp8 = {"name": "DeepSeek-FP8", "quantization": "FP8"}
    model_mlx = {"name": "Phi-MLX", "quantization": "mlx-4bit"}
    
    assert get_model_format(model_gguf) == "GGUF"
    assert get_model_format(model_awq) == "AWQ"
    assert get_model_format(model_gptq) == "GPTQ"
    assert get_model_format(model_fp8) == "FP8"
    assert get_model_format(model_mlx) == "mlx"

def test_is_format_compatible():
    # CUDA supports everything
    assert is_format_compatible("cuda", "", "GGUF") is True
    assert is_format_compatible("cuda", "", "AWQ") is True
    assert is_format_compatible("cuda", "", "FP8") is True
    
    # AMD gfx1030 rocm only supports GGUF
    assert is_format_compatible("rocm", "gfx1030", "GGUF") is True
    assert is_format_compatible("rocm", "gfx1030", "AWQ") is False
    assert is_format_compatible("rocm", "gfx1030", "FP8") is False
    
    # Standard rocm only supports GGUF by default
    assert is_format_compatible("rocm", "", "GGUF") is True
    assert is_format_compatible("rocm", "", "AWQ") is False
    
    # MLX only works on Apple Silicon
    assert is_format_compatible("mps", "", "mlx") is True
    assert is_format_compatible("cuda", "", "mlx") is False

def test_rank_models_filtering():
    # Mock system info for rocm gfx1030
    system_rocm = {
        "backend": "rocm",
        "gpu_arch": "gfx1030",
        "gpu_vram_gb": 16.0,
        "gpu_count": 1,
        "available_ram_gb": 32.0,
        "has_gpu": True
    }
    
    results_rocm = rank_models(system_rocm)
    # Ensure no AWQ/GPTQ/FP8 formats are present
    for r in results_rocm:
        assert r.get("quant") not in ("AWQ-4bit", "AWQ-8bit", "GPTQ-Int4", "GPTQ-Int8", "FP8")
        
    # Mock system info for cuda
    system_cuda = {
        "backend": "cuda",
        "gpu_vram_gb": 80.0,
        "gpu_count": 1,
        "available_ram_gb": 128.0,
        "has_gpu": True
    }
    
    results_cuda = rank_models(system_cuda)
    # Ensure AWQ or FP8 exists in the top scored models
    cuda_quants = [r.get("quant") for r in results_cuda]
    assert any(q in ("AWQ-4bit", "AWQ-8bit", "GPTQ-Int4", "GPTQ-Int8", "FP8") for q in cuda_quants)
