"""Harden hwfit model-catalog parsing against non-string field values.

`params_b` and `is_prequantized` read free-form fields straight off the HF
catalog JSON. `parameter_count` is normally a string like "7B" and
`quantization` a string like "FP8", but a catalog row can carry a non-string
(e.g. an integer parameter_count, or a null/number quantization). The code
called `pc.strip()` / `q.startswith(...)` directly, so one such row raised
AttributeError and aborted the whole ranking pass (params_b/is_prequantized
run for every model). Non-strings are now treated as unknown.
"""
from services.hwfit.models import params_b, is_prequantized
from pathlib import Path


def test_params_b_nonstring_count_does_not_raise():
    assert params_b({"parameter_count": 7}) == 0.0
    assert params_b({"parameter_count": ["7B"]}) == 0.0


def test_params_b_valid_count_still_parses():
    assert params_b({"parameter_count": "7B"}) == 7.0
    assert params_b({"parameters_raw": 7_000_000_000}) == 7.0


def test_is_prequantized_nonstring_quantization_does_not_raise():
    assert is_prequantized({"quantization": 8}) is False
    assert is_prequantized({"name": "plain-model", "quantization": 123}) is False


def test_is_prequantized_still_detects_real_markers():
    assert is_prequantized({"name": "some-model-awq"}) is True
    assert is_prequantized({"quantization": "FP8-Mixed"}) is True


def test_catalog_updater_tracks_major_official_hf_orgs():
    script = Path("scripts/add_hwfit_models.py").read_text(encoding="utf-8")
    for org in (
        "Qwen",
        "deepseek-ai",
        "zai-org",
        "MiniMaxAI",
        "moonshotai",
        "mistralai",
        "meta-llama",
        "google",
        "google-deepmind",
        "microsoft",
        "nvidia",
        "CohereLabs",
        "ai21labs",
        "Tencent-Hunyuan",
        "ibm-granite",
        "tiiuae",
        "01-ai",
        "allenai",
        "HuggingFaceTB",
        "openai",
    ):
        assert f'"{org}"' in script


def test_catalog_updater_prefilters_broad_orgs_before_expensive_probe():
    script = Path("scripts/add_hwfit_models.py").read_text(encoding="utf-8")
    assert "def _is_likely_catalog_model" in script
    assert "_GEN_MODEL_PIPELINES" in script
    assert "_GEN_MODEL_KEYWORDS" in script
    assert "BROAD_AUTHORS_SKIP_FALLBACK_PROBES" in script
    assert "probe_config=not skip_fallbacks" in script
    assert "probe_safetensors=not skip_fallbacks" in script
    assert "if not _is_likely_catalog_model(mi):" in script
    assert "continue\n            ov = EXTRA_REPOS.get(mi.id)" in script
