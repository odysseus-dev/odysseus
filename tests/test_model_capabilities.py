"""Tests for capability normalization in the model catalog loader.

Covers:
- image-text-to-text models receive 'vision' capability when missing
- any-to-any models receive both 'vision' and 'audio' capabilities
- existing capability lists are not overwritten or duplicated
- text-generation models receive no capability backfill
- get_models() returns at least one vision-capable model from the static catalog
"""

import services.hwfit.models as _models_mod
from services.hwfit.models import _normalize_model_entry, get_models


def _entry(name="test/model", pipeline_tag="text-generation", capabilities=None):
    e = {"name": name, "pipeline_tag": pipeline_tag}
    if capabilities is not None:
        e["capabilities"] = capabilities
    return e


def test_image_text_to_text_gets_vision():
    result = _normalize_model_entry(_entry(pipeline_tag="image-text-to-text"))
    assert "vision" in result["capabilities"]


def test_any_to_any_gets_vision_and_audio():
    result = _normalize_model_entry(_entry(pipeline_tag="any-to-any"))
    assert "vision" in result["capabilities"]
    assert "audio" in result["capabilities"]


def test_text_generation_unchanged():
    result = _normalize_model_entry(_entry(pipeline_tag="text-generation"))
    assert result.get("capabilities", []) == []


def test_existing_vision_not_duplicated():
    result = _normalize_model_entry(
        _entry(pipeline_tag="image-text-to-text", capabilities=["vision", "tool_use"])
    )
    assert result["capabilities"].count("vision") == 1
    assert "tool_use" in result["capabilities"]


def test_existing_capabilities_preserved_on_text_gen():
    result = _normalize_model_entry(
        _entry(pipeline_tag="text-generation", capabilities=["tool_use"])
    )
    assert result["capabilities"] == ["tool_use"]


def test_static_catalog_has_vision_models():
    _models_mod._models_cache = None
    models = get_models()
    vision_models = [m for m in models if "vision" in (m.get("capabilities") or [])]
    assert len(vision_models) > 0, "expected at least one vision-capable model in static catalog"


def test_static_catalog_vision_count_reasonable():
    _models_mod._models_cache = None
    models = get_models()
    vision_models = [m for m in models if "vision" in (m.get("capabilities") or [])]
    # The static catalog has 130+ image-text-to-text and any-to-any models.
    assert len(vision_models) >= 50
