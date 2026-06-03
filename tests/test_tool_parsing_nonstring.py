"""Regression: tool-block parsing must tolerate a non-string input.

`_normalize_dsml` did `if "DSML" not in text` (TypeError on None) and the public
`parse_tool_blocks`/`strip_tool_blocks` then ran regexes on it. Coercing a
non-string to "" in `_normalize_dsml` makes the whole chain safe.
"""
import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import _normalize_dsml, parse_tool_blocks, strip_tool_blocks


def test_non_string_does_not_crash():
    assert _normalize_dsml(None) == ""
    assert parse_tool_blocks(None) == []
    assert strip_tool_blocks(None) == ""


def test_plain_text_passes_through():
    assert strip_tool_blocks("hello world") == "hello world"
    assert parse_tool_blocks("no tools here") == []
