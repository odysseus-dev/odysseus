"""Regression guard: Opus 4.7+ rejects the temperature field entirely.

Anthropic removed the sampling parameters (temperature, top_p, top_k) starting
with Claude Opus 4.7 — sending `temperature` at all, even 0.0, returns HTTP 400.
This broke every native-Anthropic call to Opus 4.7/4.8, including the research
endpoint probe (temperature=0) and all DeepResearcher LLM calls, because
_build_anthropic_payload sent `temperature` unconditionally.

Earlier Claude models (Opus 4.6 and below, every Sonnet/Haiku) still accept
temperature in [0.0, 1.0], so the omission is version-gated — the clamp-to-[0,1]
behavior for those models (test_llm_core_anthropic_temp_clamp.py) is unchanged.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest

from src.llm_core import _anthropic_rejects_temperature, _build_anthropic_payload


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-4-7",
        "claude-opus-4-8",
        "claude-opus-4-8-20260101",  # tolerate a dated snapshot suffix
        "claude-opus-4-7-20260201",  # dated 4.7 snapshot — explicit minor, still >= 4.7
        "anthropic/claude-opus-4-7",  # tolerate a provider-prefixed id
        "claude-opus-4-10",  # future minor still >= 4.7
        "claude-opus-5-0",  # future major
    ],
)
def test_opus_47_plus_rejects_temperature(model):
    assert _anthropic_rejects_temperature(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "claude-opus-4-6",
        "claude-opus-4-5",
        "claude-opus-4-1",
        "claude-opus-4-0",
        "claude-opus-4",  # bare major (no minor) — kept
        "claude-opus-4-20250514",  # Opus 4.0 dated id — the date must NOT read as a 4.7+ minor
        "claude-opus-4-1-20250805",  # Opus 4.1 dated id — explicit minor before the date
        "claude-opus-4-6-20251201",  # dated 4.6 snapshot — older, still keeps temperature
        "claude-sonnet-4-6",
        "claude-3-5-sonnet",
        "claude-3-opus-20240229",  # legacy Claude 3 Opus — no opus-N-M pattern, kept
        "claude-haiku-4-5",
        "claude-x",
        "octopus-4-8",  # "opus" only as a substring of another word — must not match
        "myproxy/octopus-4-8",  # same, behind a provider prefix
        "",
        None,
    ],
)
def test_older_claude_models_keep_temperature(model):
    assert _anthropic_rejects_temperature(model) is False


@pytest.mark.parametrize("model", [123, 1.5, ["claude-opus-4-8"], {"a": 1}, object()])
def test_non_string_model_is_handled_without_crashing(model):
    # Defensive: the gate must not raise on a non-string model (the old builder
    # never called .lower() on it). Truthy non-strings should classify as False.
    assert _anthropic_rejects_temperature(model) is False


def _payload(model, temperature=0.0):
    return _build_anthropic_payload(
        model, [{"role": "user", "content": "hi"}], temperature, 100
    )


def test_payload_omits_temperature_for_opus_47_plus():
    # The endpoint probe sends temperature=0; on Opus 4.7+ that field must be gone.
    payload = _payload("claude-opus-4-8", 0.0)
    assert "temperature" not in payload


def test_payload_keeps_temperature_for_older_models():
    payload = _payload("claude-opus-4-6", 0.3)
    assert payload["temperature"] == 0.3
    # Older models retain the [0,1] clamp (Nietzsche preset at 1.2 -> 1.0).
    assert _payload("claude-3-5-sonnet", 1.2)["temperature"] == 1.0


def test_payload_keeps_temperature_for_dated_opus_4_0():
    # Anthropic's dated id for Opus 4.0 (claude-opus-4-20250514) is in this repo's
    # ANTHROPIC_MODELS list. The date must not be misread as a >= 4.7 minor, or the
    # user's temperature would be silently dropped on a model that accepts it.
    assert _payload("claude-opus-4-20250514", 0.5)["temperature"] == 0.5
