"""Regression: agent_tools._truncate must always return a string.

It did `len(text)` directly, so `_truncate(None)` raised TypeError. Returning
the raw non-string just moves the crash downstream (callers treat it as text),
so non-strings are now coerced to a string and still truncated.
"""
from src.agent_tools import _truncate


def test_non_string_coerced_to_string():
    assert _truncate(None) == ""
    assert _truncate(123) == "123"
    assert isinstance(_truncate({"a": 1}), str)


def test_non_string_is_also_truncated():
    out = _truncate(12345, limit=3)
    assert out.startswith("1")
    assert out.endswith("45")
    assert "chars omitted" in out


def test_string_truncation_unchanged():
    assert _truncate("hello", limit=100) == "hello"
    out = _truncate("x" * 50, limit=10)
    assert out.startswith("x" * 5)
    assert out.endswith("x" * 5)
    assert "chars omitted" in out
