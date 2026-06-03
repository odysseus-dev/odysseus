"""_native_quant must emit canonical quant labels that key the cost maps.

services/hwfit/models.py keys QUANT_BPP and QUANT_QUALITY_PENALTY on
"GPTQ-Int4"/"GPTQ-Int8" and "AWQ-4bit"/"AWQ-8bit". _native_quant returned
"GPTQ-4bit" (and bare "AWQ" when no digit), which miss both maps, so a
pre-quantized GPTQ/AWQ model fell back to the default BPP (0.58 instead of
0.50) and a zero quality penalty, over-estimating VRAM and inflating the
score. The label is also shown in the UI and disagreed with the catalog.
"""
from services.hwfit.fit import _native_quant
from services.hwfit.models import QUANT_BPP, QUANT_QUALITY_PENALTY


def test_gptq_int4_label_is_canonical():
    label = _native_quant({"name": "Qwen2.5-32B-Instruct-GPTQ-Int4"})
    assert label == "GPTQ-Int4"
    assert label in QUANT_BPP and label in QUANT_QUALITY_PENALTY


def test_gptq_int8_label_is_canonical():
    label = _native_quant({"name": "x-GPTQ-Int8"})
    assert label == "GPTQ-Int8"
    assert label in QUANT_BPP and label in QUANT_QUALITY_PENALTY


def test_awq_no_digit_falls_back_to_canonical():
    label = _native_quant({"name": "SomeModel-AWQ"})
    assert label == "AWQ-4bit"
    assert label in QUANT_BPP and label in QUANT_QUALITY_PENALTY


def test_awq_with_digit_is_canonical():
    label = _native_quant({"name": "x-AWQ-8bit"})
    assert label == "AWQ-8bit"
    assert label in QUANT_BPP and label in QUANT_QUALITY_PENALTY


def test_gptq_fallback_label_is_in_maps():
    # GPTQ mentioned with no parseable bit-width
    label = _native_quant({"name": "model-gptq", "format": ""})
    assert label == "GPTQ-Int4"
    assert label in QUANT_BPP and label in QUANT_QUALITY_PENALTY
