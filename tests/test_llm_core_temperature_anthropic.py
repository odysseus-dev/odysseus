"""Regression tests: Anthropic temperature clamping.

Anthropic rejects temperature values outside [0.0, 1.0]. The payload builder
must clamp the value to that range before sending rather than letting the API
return HTTP 400.
"""
from src import llm_core


def _anthropic_payload(temperature):
    return llm_core._build_anthropic_payload(
        "claude-3-5-sonnet",
        [{"role": "user", "content": "Hi"}],
        temperature,
        max_tokens=5,
    )


def test_anthropic_payload_clamps_above_one():
    # Anthropic rejects temperature > 1.0 (e.g. the Nietzsche preset's 1.2).
    assert _anthropic_payload(1.2)["temperature"] == 1.0


def test_anthropic_payload_keeps_in_range():
    assert _anthropic_payload(0.7)["temperature"] == 0.7


def test_anthropic_payload_clamps_negative():
    assert _anthropic_payload(-0.5)["temperature"] == 0.0


def test_anthropic_payload_none_temperature_does_not_crash():
    payload = _anthropic_payload(None)
    assert payload["temperature"] is None
