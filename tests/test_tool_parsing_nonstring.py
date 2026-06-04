"""Regression: tool-block parsing must tolerate a non-string input.

`_normalize_dsml` did `if "DSML" not in text` (TypeError on None) and the public
`parse_tool_blocks`/`strip_tool_blocks` then ran regexes on it. Coercing a
non-string to "" in `_normalize_dsml` makes the whole chain safe.
"""
import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import _normalize_dsml, parse_tool_blocks, strip_tool_blocks
from src.tool_schemas import function_call_to_tool_block


def test_non_string_does_not_crash():
    assert _normalize_dsml(None) == ""
    assert parse_tool_blocks(None) == []
    assert strip_tool_blocks(None) == ""


def test_plain_text_passes_through():
    assert strip_tool_blocks("hello world") == "hello world"
    assert parse_tool_blocks("no tools here") == []


def test_parse_bare_function_style_web_fetch():
    blocks = parse_tool_blocks('web_fetch("https://www.iana.org/help/example-domains")')

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_fetch"
    assert blocks[0].content == "https://www.iana.org/help/example-domains"
    assert strip_tool_blocks('web_fetch("https://www.iana.org/help/example-domains")') == ""


def test_parse_bare_command_style_web_fetch():
    text = 'web_fetch url="https://www.iana.org/help/example-domains"'
    blocks = parse_tool_blocks(text)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_fetch"
    assert blocks[0].content == "https://www.iana.org/help/example-domains"
    assert strip_tool_blocks(text) == ""


def test_parse_bare_command_style_web_search():
    blocks = parse_tool_blocks('web_search query="example domains IANA"')

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "example domains IANA"


def test_native_web_fetch_function_call_converts_to_tool_block():
    block = function_call_to_tool_block(
        "web_fetch",
        '{"url": "https://www.iana.org/help/example-domains"}',
    )

    assert block is not None
    assert block.tool_type == "web_fetch"
    assert block.content == "https://www.iana.org/help/example-domains"
