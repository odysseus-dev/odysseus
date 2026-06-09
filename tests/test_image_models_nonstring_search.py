from services.hwfit.image_models import rank_image_models, IMAGE_MODEL_REGISTRY

SYS = {"gpu_vram_gb": 0, "has_gpu": False}


def test_rank_image_models_handles_non_string_search():
    # search is a CLI/API filter arg; a non-string made search.lower() raise
    # AttributeError. A non-string search should behave as "no filter".
    out = rank_image_models(SYS, search=123)
    assert len(out) == len(IMAGE_MODEL_REGISTRY)


def test_rank_image_models_string_filter_still_applies():
    out = rank_image_models(SYS, search="zzzznotarealmodelzzz")
    assert out == []
