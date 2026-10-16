from services.hwfit.fit import rank_models
from services.hwfit.models import get_models, is_prequantized


def _8gb_vram_system():
    return {
        "has_gpu": True,
        "backend": "cuda",
        "gpu_name": "NVIDIA GeForce RTX 4060",
        "gpu_vram_gb": 8.0,
        "gpu_count": 1,
        "available_ram_gb": 32.0,
        "total_ram_gb": 32.0,
    }


def test_gemma4_12b_in_catalog():
    catalog = {m["name"]: m for m in get_models()}
    assert "google/gemma-4-12B-it" in catalog, "gemma-4-12B-it missing from catalog"


def test_gemma4_12b_has_gguf_source():
    catalog = {m["name"]: m for m in get_models()}
    entry = catalog["google/gemma-4-12B-it"]
    assert entry.get("gguf_sources"), "gemma-4-12B-it has no gguf_sources"
    repos = [s["repo"] for s in entry["gguf_sources"]]
    assert "unsloth/gemma-4-12B-it-GGUF" in repos


def test_gemma4_12b_rank_models_returns_it_for_8gb_vram():
    results = rank_models(_8gb_vram_system(), search="gemma-4-12B-it", limit=20)
    names = [r["name"] for r in results]
    assert "google/gemma-4-12B-it" in names, "rank_models did not return gemma-4-12B-it for 8 GB VRAM"


def test_gemma4_12b_qat_entries_in_catalog():
    catalog = {m["name"]: m for m in get_models()}
    assert "google/gemma-4-12B-it-qat-int4" in catalog
    assert "google/gemma-4-12B-it-qat-int8" in catalog


def test_gemma4_12b_qat_entries_are_prequantized():
    catalog = {m["name"]: m for m in get_models()}
    assert is_prequantized(catalog["google/gemma-4-12B-it-qat-int4"])
    assert is_prequantized(catalog["google/gemma-4-12B-it-qat-int8"])


def test_gemma4_12b_qat_entries_have_no_gguf():
    catalog = {m["name"]: m for m in get_models()}
    assert catalog["google/gemma-4-12B-it-qat-int4"]["gguf_sources"] == []
    assert catalog["google/gemma-4-12B-it-qat-int8"]["gguf_sources"] == []
