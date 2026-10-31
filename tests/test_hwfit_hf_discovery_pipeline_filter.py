import pytest

from services.hwfit.hf_discovery import _entry_from_collection_item

_SOURCE = {"owner": "google", "provider": "Google"}
_COLLECTION = {"title": "collection"}


@pytest.mark.parametrize("repo_id,pipeline_tag", [
    ("google/timesfm-3.0-pytorch", "time-series-forecasting"),
    ("google/siglip2-base-patch16-224", "zero-shot-image-classification"),
    ("moonshotai/MoonViT-SO-400M", "image-feature-extraction"),
    ("zai-org/CogView4-6B", "text-to-image"),
    ("zai-org/CogVideoX-5b-I2V", "image-to-video"),
    ("zai-org/CogVideoX1.5-5B", "text-to-video"),
])
def test_non_llm_collection_models_are_skipped(repo_id, pipeline_tag):
    item = {"id": repo_id, "type": "model", "pipeline_tag": pipeline_tag, "numParameters": 1_000_000_000}
    assert _entry_from_collection_item(_COLLECTION, item, _SOURCE) is None


@pytest.mark.parametrize("repo_id,pipeline_tag", [
    ("google/gemma-4-12B-it", "image-text-to-text"),
    ("Qwen/Qwen3.6-27B", "text-generation"),
    ("Qwen/Qwen3-Embedding-4B", "feature-extraction"),
    ("NousResearch/DeepHermes-Egregore-v2-RLAIF-8b-Atropos", "reinforcement-learning"),
    ("Qwen/Qwen3-Next-80B-A3B-Instruct", ""),
])
def test_servable_collection_models_are_kept(repo_id, pipeline_tag):
    item = {"id": repo_id, "type": "model", "pipeline_tag": pipeline_tag, "numParameters": 4_000_000_000}
    assert _entry_from_collection_item(_COLLECTION, item, _SOURCE) is not None
