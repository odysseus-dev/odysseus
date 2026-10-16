from services.hwfit.fit import analyze_model, rank_models
from services.hwfit.models import (
    get_models,
    infer_quantization_from_name,
    is_prequantized,
)


def _dual_5060ti_system():
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA GeForce RTX 5060 Ti",
        "gpu_vram_gb": 31.0,
        "gpu_count": 2,
        "available_ram_gb": 128.0,
        "total_ram_gb": 128.0,
    }


def test_infers_native_hf_quant_formats_from_repo_names():
    cases = {
        "txn545/Qwen3.5-122B-A10B-NVFP4": "NVFP4",
        "some/model-MXFP4": "MXFP4",
        "some/model-NF4": "NF4",
        "some/model-FP4": "FP4",
        "some/model-W4A16": "W4A16",
        "some/model-W8A8": "W8A8",
        "some/model-W8A16": "W8A16",
        "some/model-INT4": "INT4",
        "some/model-8bit": "INT8",
    }
    assert {name: infer_quantization_from_name(name) for name in cases} == cases


def test_nvfp4_catalog_quant_is_preserved():
    catalog = {m["name"]: m for m in get_models()}
    model = catalog["txn545/Qwen3.5-122B-A10B-NVFP4"]

    assert model["quantization"] == "NVFP4"
    assert is_prequantized(model)


def test_nvfp4_search_result_is_not_gguf_or_cpu_offload():
    catalog = {m["name"]: m for m in get_models()}
    model = catalog["txn545/Qwen3.5-122B-A10B-NVFP4"]

    fit = analyze_model(model, _dual_5060ti_system())
    assert fit["quant"] == "NVFP4"
    assert fit["run_mode"] != "cpu_offload"

    results = rank_models(
        _dual_5060ti_system(),
        search="Qwen3.5-122B-A10B-NVFP4",
        limit=10,
    )
    hit = next(r for r in results if r["name"] == "txn545/Qwen3.5-122B-A10B-NVFP4")
    assert hit["quant"] == "NVFP4"
    assert hit["run_mode"] != "cpu_offload"


def test_selected_gguf_quant_is_strict_not_lower_quant_fallback():
    model = {
        "name": "local/Huge-GGUF",
        "provider": "local",
        "parameter_count": "100B",
        "parameters_raw": 100_000_000_000,
        "quantization": "Q4_K_M",
        "context_length": 4096,
    }

    system = _dual_5060ti_system()
    system["available_ram_gb"] = 80.0
    system["total_ram_gb"] = 80.0
    fit = analyze_model(model, system, target_quant="Q8_0")

    assert fit["quant"] == "Q8_0"
    assert fit["run_mode"] == "no_fit"
