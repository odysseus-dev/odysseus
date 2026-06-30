"""Tool-output display truncation uses _truncate with an indicator.

Previously agent_loop sliced tool output to a hard character limit ([:2000]
or [:4000]) with no signal to the UI that data was lost.  Now it delegates to
tool_utils._truncate which caps at MAX_OUTPUT_CHARS (10 000) and appends
a ``... (truncated, N chars total)`` suffix so the frontend can show a
truncation indicator in the tool bubble.
"""
from src.tool_utils import _truncate, MAX_OUTPUT_CHARS


def test_short_output_unchanged():
    """Outputs within the limit pass through verbatim."""
    text = "hello world"
    assert _truncate(text) == text


def test_long_output_truncated_with_indicator():
    """Outputs exceeding MAX_OUTPUT_CHARS are truncated with a suffix."""
    text = "x" * (MAX_OUTPUT_CHARS + 500)
    result = _truncate(text)
    assert len(result) > MAX_OUTPUT_CHARS  # includes suffix
    assert result.startswith("x" * MAX_OUTPUT_CHARS)
    assert "truncated" in result
    assert str(len(text)) in result  # original length reported


def test_exact_limit_unchanged():
    """An output exactly at the limit is not truncated."""
    text = "a" * MAX_OUTPUT_CHARS
    assert _truncate(text) == text


def test_default_limit_matches_constant():
    """_truncate default limit equals MAX_OUTPUT_CHARS (10 000)."""
    assert MAX_OUTPUT_CHARS == 10_000
    text = "y" * 10_001
    result = _truncate(text)
    assert "truncated" in result


def test_empty_string():
    assert _truncate("") == ""
