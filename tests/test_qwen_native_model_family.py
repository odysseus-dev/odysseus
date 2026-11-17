"""Qwen3.6/Qwen3.8 Odysseus-native model-family regressions."""

import pytest

from src import llm_core
from src.agent_loop import _is_odysseus_qwen_native


@pytest.mark.parametrize(
    "model",
    [
        "qwen36-27b-mlx",
        "Qwen3.6-27B-MLX-8bit",
        "qwen38-27b-mlx",
        "Qwen3.8-27B-MLX-Q4",
        "openai//Users/pewds/models/Qwen3.8-27B-FP8",
    ],
)
def test_qwen_native_family_accepts_supported_name_variants(model):
    assert _is_odysseus_qwen_native(model) is True
    assert llm_core._is_odysseus_qwen_native_model(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "Qwen3-27B-MLX",
        "Qwen3.8-14B-MLX",
        "Qwen3.6-27B-AWQ",
        "qwen36-terminal-lora",
        "llama3.1-27b-mlx",
        "",
        None,
    ],
)
def test_qwen_native_family_rejects_other_deployments(model):
    assert _is_odysseus_qwen_native(model) is False
    assert llm_core._is_odysseus_qwen_native_model(model) is False


@pytest.mark.parametrize("model", ["qwen36-27b-mlx", "qwen38-27b-mlx"])
def test_qwen_native_generation_defaults_apply_to_both_families(model):
    payload = {"model": model, "temperature": 0.8, "max_tokens": 4096}

    llm_core._apply_local_generation_stability(
        payload,
        "http://192.168.1.21:8070/v1/chat/completions",
        model,
    )

    assert payload["temperature"] == 0.0
    assert payload["top_p"] == 1.0
    assert payload["top_k"] == 0
    assert payload["max_tokens"] == 1024
