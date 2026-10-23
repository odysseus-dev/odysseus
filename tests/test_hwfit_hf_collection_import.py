"""Collection metadata should describe servable models and their active size."""

import pytest

from services.hwfit.fit import _estimate_speed
from services.hwfit.hf_discovery import _entry_from_collection_item


def _entry(repo_id="Qwen/Example-8B", **metadata):
    item = {"type": "model", "id": repo_id, **metadata}
    return _entry_from_collection_item({"title": "Examples"}, item, {"provider": "example"})


@pytest.mark.parametrize(("repo_id", "reported", "active"), [
    ("Qwen/Qwen3.6-35B-A3B", 35_950_000_000, 3_000_000_000),
    ("google/gemma-4-26B-A4B-it", 25_810_000_000, 4_000_000_000),
])
def test_reported_total_keeps_independent_moe_active_size(repo_id, reported, active):
    model = _entry(repo_id, numParameters=reported, pipeline_tag="text-generation")

    assert model["parameters_raw"] == reported
    assert model["is_moe"] is True
    assert model["active_parameters"] == active
    assert model["min_ram_gb"] == round(reported / 1e9 * 2.2 + 0.8, 1)
    system = {"backend": "cuda", "gpu_name": "NVIDIA GeForce RTX 3090"}
    dense = {key: value for key, value in model.items() if key not in {"is_moe", "active_parameters"}}
    assert _estimate_speed(model, "Q4_K_M", "gpu", system) > _estimate_speed(dense, "Q4_K_M", "gpu", system)


def test_name_size_fallback_still_preserves_moe_metadata():
    model = _entry("Qwen/Example-35B-A3B")
    assert model["parameters_raw"] == 35_000_000_000
    assert model["active_parameters"] == 3_000_000_000
    assert model["is_moe"] is True


def test_dense_model_keeps_reported_total_without_moe_fields():
    model = _entry(numParameters=8_050_000_000)
    assert model["parameters_raw"] == 8_050_000_000
    assert "is_moe" not in model
    assert "active_parameters" not in model


@pytest.mark.parametrize("pipeline_tag", [
    "time-series-forecasting", "zero-shot-image-classification",
    "image-feature-extraction", "text-to-image", "image-to-video",
    "text-to-video", "image-to-image", "image-classification",
    "object-detection", "text-to-audio", "unknown-new-task",
])
def test_non_language_pipeline_is_not_imported(pipeline_tag):
    assert _entry(numParameters=8_000_000_000, pipeline_tag=pipeline_tag) is None


@pytest.mark.parametrize("pipeline_tag", [
    "text-generation", "text2text-generation", "image-text-to-text",
    "audio-text-to-text", "any-to-any", "feature-extraction",
    "sentence-similarity", "text-ranking", "text-classification",
    "token-classification", "automatic-speech-recognition", "text-to-speech",
])
def test_servable_pipeline_remains_eligible(pipeline_tag):
    assert _entry(pipeline_tag=pipeline_tag) is not None


def test_reinforcement_learning_deephermes_is_retained():
    model = _entry("NousResearch/DeepHermes-3-Mistral-24B-Preview", pipeline_tag="reinforcement-learning")
    assert model is not None


@pytest.mark.parametrize("metadata", [{}, {"pipeline_tag": None}, {"pipeline_tag": ""}])
def test_missing_pipeline_tag_does_not_discard_sized_models(metadata):
    assert _entry(**metadata) is not None


def test_missing_pipeline_and_parameter_count_cannot_invent_model_size():
    assert _entry("example/Unspecified") is None
