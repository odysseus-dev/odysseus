from services.hwfit.fit import _model_matches_search, rank_models


def test_aa_slug_search_strips_instruct_suffix():
    model = {"name": "Qwen/Qwen3-8B-AWQ", "provider": "Alibaba"}
    assert _model_matches_search(model, "qwen3-8b-instruct") is True
    assert _model_matches_search(model, "qwen3-8b") is True
    assert _model_matches_search(model, "llama-3") is False


def test_rank_models_finds_qwen3_8b_via_aa_slug():
    system = {"has_gpu": True, "gpu_vram_gb": 24, "ram_gb": 32, "backend": "cuda"}
    results = rank_models(system, search="qwen3-8b-instruct", limit=900)
    names = {r["name"] for r in results}
    assert "Qwen/Qwen3-8B" in names
    assert "deepseek-ai/DeepSeek-R1-0528-Qwen3-8B" not in names
