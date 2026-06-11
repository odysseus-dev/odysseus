"""Canonical _truncate must tolerate non-string input (regression).

Originally this tested mcp_servers/_common.py's copy, which was deleted
since it had zero callers. Now it tests the canonical version.
"""

from src.tool_utils import _truncate

def test_truncate_handles_none_and_nonstring():
    assert _truncate(None) == ""       # pyright: ignore[reportArgumentType]
    assert _truncate(12345) == "12345" # pyright: ignore[reportArgumentType]


def test_truncate_string_behaviour_unchanged():
    assert _truncate("hello", limit=100) == "hello"
    out = _truncate("x" * 50, limit=10)
    assert out.startswith("x" * 10) and "truncated" in out
