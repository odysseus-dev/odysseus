"""Regression: gpt-oss OpenAI-harmony analysis-channel tokens must not leak into chat."""

from src.text_helpers import normalize_harmony_markup, strip_think
from routes.chat_helpers import _normalize_thinking, _extract_thinking_meta


def test_analysis_and_final_channels_split():
    raw = (
        "<|channel|>analysis<|message|>We are inside Odysseus. We want to list files.<|end|>"
        "<|channel|>final<|message|>Here are the files in /:<|return|>"
    )
    out = normalize_harmony_markup(raw)
    assert "<|channel|>" not in out
    assert "We are inside Odysseus" in out
    assert "Here are the files" in out
    assert "<think>" in out


def test_unclosed_analysis_becomes_thinking():
    raw = "<|channel|>analysis<|message|>Still reasoning about the request"
    out = normalize_harmony_markup(raw)
    assert out.startswith("<think>")
    assert "Still reasoning" in out
    assert "<|channel|>" not in out


def test_strip_think_removes_harmony_analysis_from_visible():
    raw = (
        "<|channel|>analysis<|message|>Internal plan.<|end|>"
        "<|channel|>final<|message|>The answer.<|return|>"
    )
    visible = strip_think(raw)
    assert "Internal plan" not in visible
    assert "The answer" in visible


def test_chat_helpers_extract_harmony_thinking_meta():
    raw = (
        "<|channel|>analysis<|message|>User wants root listing.<|end|>"
        "<|channel|>final<|message|>bin etc home<|return|>"
    )
    normalized = _normalize_thinking(raw)
    meta = _extract_thinking_meta(normalized)
    assert meta is not None
    assert "root listing" in meta["thinking"]
    assert "bin etc" in meta["reply"]
