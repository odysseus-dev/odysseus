"""Regression: is_public_blocked_tool must tolerate a non-string tool name.

The `if not tool_name` guard only handled falsy values; a truthy non-string
(e.g. 5 or a list) reached `tool_name.startswith("mcp__")` / the set membership
test and raised AttributeError/TypeError. Non-strings now return False
(consistent with the falsy passthrough).
"""
from src.tool_security import is_public_blocked_tool


def test_non_string_returns_false():
    assert is_public_blocked_tool(5) is False
    assert is_public_blocked_tool(["bash"]) is False
    assert is_public_blocked_tool(None) is False


def test_real_tool_names_still_classified():
    assert is_public_blocked_tool("mcp__whatever") is True
