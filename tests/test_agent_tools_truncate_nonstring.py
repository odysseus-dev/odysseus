"""Regression: agent_tools._truncate must tolerate non-string input.

It did `len(text)` directly, so `_truncate(None)` raised TypeError. Non-strings
now pass through unchanged.
"""
from src.agent_tools import _truncate


def test_non_string_passthrough():
    assert _truncate(None) is None
    assert _truncate(123) == 123


def test_string_truncation_unchanged():
    assert _truncate("hello", limit=100) == "hello"
    out = _truncate("x" * 50, limit=10)
    assert out.startswith("x" * 10) and "truncated" in out
