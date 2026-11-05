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

import pytest

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


# Rows shaped like the two catalogs. The shipped hf_models.json and
# mlx_community_models.json are empty lists by design — see the README beside
# them: a fresh install has no recommendations until discovery populates the
# runtime cache. So the properties below are asserted against rows the test
# owns. Asserting them against whatever the repository happens to ship would
# pass only on an installation that has already been populated.
_CATALOG_SHAPES = [
    # mlx_community_models.json shape: clean use_case, tag present.
    {"name": "mlx-community/gemma-3-27b-it-qat-4bit", "pipeline_tag": "image-text-to-text",
     "use_case": "multimodal", "format": "mlx", "quantization": "mlx-4bit"},
    # hf_models.json shape: use_case is free prose, so the tag is the only
    # signal the two catalogs share.
    {"name": "google/gemma-4-26B-A4B-it", "pipeline_tag": "image-text-to-text",
     "use_case": "General purpose, multimodal", "quantization": "BF16"},
    {"name": "QuantTrio/Qwen3-VL-30B-A3B-Instruct-AWQ", "pipeline_tag": "text-generation",
     "use_case": "Vision, chat, coding, agentic tool use", "quantization": "AWQ-4bit"},
    {"name": "unsloth/gemma-4-12B-it-GGUF", "pipeline_tag": "image-text-to-text",
     "use_case": "General purpose, multimodal", "quantization": "Q4_K_M"},
]


def test_every_tagged_entry_is_marked_whatever_the_catalog_shape():
    for entry in _CATALOG_SHAPES:
        assert _normalize_model_entry(dict(entry))["modality"] == "vision", entry["name"]


def test_the_marker_does_not_sweep_up_text_models():
    # Precision guard. These are real catalog names of text models; several sit
    # one edit away from a marker ("vl" inside a word, a vision-adjacent
    # family), which is exactly where a loosened rule would start leaking.
    for name in (
        "mlx-community/Qwen3-8B-4bit",
        "mlx-community/Llama-3.3-70B-Instruct-4bit",
        "mlx-community/DeepSeek-V4-Flash-4bit",
        "mlx-community/GLM-Z1-32B-0414-4bit",
        "lmsys/vicuna-13b-v1.5",
        "some/navlm-7b",
        "some/revlon-3b",
        "mlx-community/Molmo-7B-D-0924-8bit-skip-vision",
    ):
        entry = _normalize_model_entry({"name": name, "pipeline_tag": "text-generation"})
        assert entry["modality"] != "vision", name


def _text_name_corpus():
    """A few hundred plausible text-model ids, combined from parts.

    Generated rather than copied: the project deliberately ships no upstream
    catalog rows (see the README beside the data files), and a long list of
    real ids would be exactly that. None of the parts below contains a vision
    marker, so the expected answer for the whole corpus is "no vision" — which
    makes this sharper than the share check it replaces. A share threshold
    tolerates a few wrong answers; here any single one is a defect.

    It guards the direction the eight named cases cannot: a rule loosened for a
    CLASS of names nobody enumerated. That direction used to be covered only by
    "vision stays a minority of the catalog", which can no longer run where the
    catalogs are empty.
    """
    vendors = ("acme", "northwind", "contoso", "initech", "umbrella", "hooli")
    families = ("falcon", "corvid", "mistralish", "orca", "zephyr", "tulip",
                "gemstone", "lumen", "cobalt", "basalt")
    sizes = ("1b", "3b", "7b", "8b", "13b", "27b", "32b", "70b", "120b")
    suffixes = ("", "-instruct", "-chat", "-it", "-base", "-4bit", "-8bit",
                "-q4-k-m", "-bf16", "-awq", "-gguf")
    return [f"{v}/{f}-{s}{x}"
            for v in vendors for f in families for s in sizes for x in suffixes][:600]


def test_no_text_name_in_a_generated_corpus_is_read_as_vision():
    corpus = _text_name_corpus()
    assert len(corpus) >= 500, "corpus too small to say anything"
    flagged = [n for n in corpus
               if _normalize_model_entry({"name": n})["modality"] == "vision"]
    assert flagged == [], f"{len(flagged)} of {len(corpus)} text names read as vision, e.g. {flagged[:5]}"


def test_the_corpus_would_notice_a_rule_that_stopped_discriminating():
    # Counterpart to the above: the same corpus with a marker spliced into each
    # name must come back entirely vision. Without this, a rule that answered
    # "text" to everything would sail through the test above.
    corpus = [n.replace("/", "/qwen2.5-vl-", 1) for n in _text_name_corpus()[:50]]
    missed = [n for n in corpus
              if _normalize_model_entry({"name": n})["modality"] != "vision"]
    assert missed == [], missed[:5]


def test_the_same_properties_hold_on_whatever_rows_are_present():
    # Bonus pass over the real catalogs when an installation has them: it is
    # the only check that sees rows nobody wrote a case for. It cannot be the
    # primary guard, since upstream ships none.
    rows = get_models()
    if not rows:
        pytest.skip("shipped catalogs are empty by design — nothing to cross-check")
    tagged = [m for m in rows if m.get("pipeline_tag") == "image-text-to-text"]
    assert [m["name"] for m in tagged if m.get("modality") != "vision"] == []
    vision = [m for m in rows if m.get("modality") == "vision"]
    assert len(vision) < len(rows) / 3, "vision should stay a minority; the marker may have loosened"


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
