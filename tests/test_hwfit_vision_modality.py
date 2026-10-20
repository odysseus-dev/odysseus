"""The catalog must tell the UI which models are vision models.

`analyze_model()` returns an explicit field whitelist, and `pipeline_tag` is
not in it — so the tag that distinguishes the 260 `image-text-to-text` entries
never reached the browser, and the serve logic had nothing to branch on. The
fix normalizes the tag into a derived `modality` field at catalog load and
carries that one field through the fit result.

`modality` is catalog truth (what the model declares it can do). It is
deliberately NOT a statement about whether this machine can serve it — that is
derived at runtime from whether mlx-vlm is installed in the launch interpreter.
"""

from services.hwfit.fit import analyze_model
from services.hwfit.models import _normalize_model_entry, get_models
from services.hwfit.vision import vision_modality


def _metal_system():
    return {
        "has_gpu": True,
        "backend": "metal",
        "gpu_name": "Apple M4 Max",
        "gpu_vram_gb": 96.0,
        "gpu_count": 1,
        "available_ram_gb": 89.6,
        "total_ram_gb": 128.0,
        "unified_memory": True,
    }


def test_pipeline_tag_is_normalized_into_modality():
    entry = _normalize_model_entry({
        "name": "mlx-community/gemma-3-27b-it-qat-4bit",
        "pipeline_tag": "image-text-to-text",
    })
    assert entry["modality"] == "vision"


def test_text_models_are_not_marked_vision():
    entry = _normalize_model_entry({
        "name": "mlx-community/Qwen3-8B-4bit",
        "pipeline_tag": "text-generation",
    })
    assert entry.get("modality") != "vision"


def test_name_marker_recovers_mistagged_vision_models():
    # Real catalog row: LFM2-VL is a vision-language model but ships tagged
    # text-generation. The name marker is the only signal that catches it.
    entry = _normalize_model_entry({
        "name": "mlx-community/LFM2-VL-450M-6bit",
        "pipeline_tag": "text-generation",
    })
    assert entry["modality"] == "vision"


def test_skip_vision_stays_text():
    entry = _normalize_model_entry({
        "name": "mlx-community/Molmo-7B-D-0924-8bit-skip-vision",
        "pipeline_tag": "image-text-to-text",
    })
    assert entry.get("modality") != "vision"


def test_modality_survives_the_fit_result_whitelist():
    model = _normalize_model_entry({
        "name": "mlx-community/gemma-3-27b-it-qat-4bit",
        "pipeline_tag": "image-text-to-text",
        "parameter_count": "27B",
        "parameters_raw": 27_000_000_000,
        "quantization": "mlx-4bit",
        "context_length": 32768,
        "format": "mlx",
    })
    fit = analyze_model(model, _metal_system())
    assert fit["modality"] == "vision"


def test_text_model_fit_result_reports_text_modality():
    model = _normalize_model_entry({
        "name": "mlx-community/Qwen3-8B-4bit",
        "pipeline_tag": "text-generation",
        "parameter_count": "8B",
        "parameters_raw": 8_000_000_000,
        "quantization": "mlx-4bit",
        "context_length": 32768,
        "format": "mlx",
    })
    fit = analyze_model(model, _metal_system())
    assert fit["modality"] != "vision"


def test_every_tagged_catalog_entry_is_marked_across_both_catalogs():
    # hf_models.json has no usable `use_case` for this (free text like
    # "General purpose"), so pipeline_tag is the only cross-catalog signal.
    # Guard that the derivation runs for every row, not just the MLX one.
    tagged = [m for m in get_models() if m.get("pipeline_tag") == "image-text-to-text"]
    assert tagged, "catalogs should carry image-text-to-text entries"
    unmarked = [m["name"] for m in tagged if m.get("modality") != "vision"]
    assert unmarked == []


def test_vision_detection_does_not_leak_into_the_bulk_of_the_catalog():
    # Precision guard: the name marker must not sweep up text models. Vision
    # is a minority of the catalog; if this ever flips, the marker regressed.
    rows = get_models()
    vision = [m for m in rows if m.get("modality") == "vision"]
    assert 0 < len(vision) < len(rows) / 3


# ── The containing directory is not the model ──
#
# `path` never carries the model's identity: the HF-cache scan sets it to the
# cache root, the local-directory scan sets it to the PARENT of the model
# folder (the folder's own name is repo_id), Ollama rows set it to the literal
# "ollama", and no catalog row has the field at all. Matching the vision marker
# against it therefore reads a user's folder naming as if it were model
# metadata — in both directions.


def test_a_directory_name_does_not_make_a_text_model_vision():
    for folder in (
        "/Users/x/vision-models",
        "/Users/x/llava-experiments",
        "/mnt/vl/models",
        "/data/pixtral-tests",
        "/srv/vision",
    ):
        entry = {"repo_id": "Qwen3-8B-4bit", "path": folder}
        assert vision_modality(entry) != "vision", folder


def test_a_directory_name_does_not_suppress_a_real_vision_model():
    # The worse direction: a folder called "skip-vision-archive" would send a
    # genuine VLM back to the text-only server — the exact bug this module
    # exists to prevent, reintroduced by how the user names a directory.
    entry = {"repo_id": "Qwen2.5-VL-3B-Instruct-4bit", "path": "/Users/x/skip-vision-archive"}
    assert vision_modality(entry) == "vision"


def test_the_model_s_own_identity_is_still_read_from_repo_id():
    # Local rows identify the model by repo_id — that must keep working, or
    # dropping `path` would trade one hole for another.
    assert vision_modality({"repo_id": "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"}) == "vision"
    assert vision_modality({"repo_id": "Qwen2.5-VL-7B-Instruct-8bit",
                            "path": "/Users/x/models"}) == "vision"
