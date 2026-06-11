"""Regression guard for #1615 — Anthropic temperature must be clamped to [0.0, 1.0].

Anthropic's Messages API rejects temperature > 1.0 with HTTP 400. The shipped
"Nietzsche" preset uses temperature 1.2 (static/js/presets.js) and the UI slider
allows up to 2.0 (static/index.html), so _build_anthropic_payload must clamp into
[0.0, 1.0]. The clamp lives only in the Anthropic builder — OpenAI keeps its
wider 0.0-2.0 range.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from src.llm_core import _build_anthropic_payload


def _temp(t):
    payload = _build_anthropic_payload(
        "claude-x", [{"role": "user", "content": "hi"}], t, 100
    )
    return payload["temperature"]


def test_above_range_is_clamped_to_one():
    assert _temp(1.2) == 1.0  # the shipped "Nietzsche" preset — previously 400'd
    assert _temp(2.0) == 1.0  # UI slider max


def test_in_range_is_unchanged():
    assert _temp(0.0) == 0.0
    assert _temp(0.7) == 0.7
    assert _temp(1.0) == 1.0


def test_below_range_is_clamped_to_zero():
    assert _temp(-0.5) == 0.0


def test_none_is_passed_through_unchanged():
    # Callers may pass None; behavior is unchanged (no clamp, no crash).
    assert _temp(None) is None
