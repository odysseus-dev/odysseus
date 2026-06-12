"""Regression: is_public_blocked_tool must fail CLOSED on a non-string tool name.

The `if not tool_name` guard only handled falsy values; a truthy non-string
(e.g. 5 or a list) reached `tool_name.startswith("mcp__")` and raised
AttributeError/TypeError. Because this is a public-execution security gate, a
malformed (non-string) identifier must be treated as BLOCKED, not silently
allowed. None/empty mean there is no tool to gate.
"""
from src.tool_security import is_public_blocked_tool


def test_malformed_non_string_name_is_blocked():
    # Fail closed: a non-string identifier cannot be validated, so block it.
    assert is_public_blocked_tool(5) is True
    assert is_public_blocked_tool(["bash"]) is True
    assert is_public_blocked_tool({"x": 1}) is True


def test_none_or_empty_is_not_gated():
    assert is_public_blocked_tool(None) is False
    assert is_public_blocked_tool("") is False


def test_real_tool_names_still_classified():
    assert is_public_blocked_tool("mcp__whatever") is True
