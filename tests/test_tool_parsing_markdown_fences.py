"""Regression tests for fenced tool parsing vs ordinary Markdown examples."""

import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks


def test_bare_fenced_bash_still_executes_as_tool_call():
    text = "```bash\nls -la\n```"

    blocks = parse_tool_blocks(text)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "bash"
    assert blocks[0].content == "ls -la"
    assert strip_tool_blocks(text) == ""


def test_markdown_bash_example_is_not_executed_or_stripped():
    text = "Here is a shell example:\n\n```bash\nrm -rf /tmp/example\n```"

    assert parse_tool_blocks(text) == []
    assert strip_tool_blocks(text) == text


def test_explicit_tool_preamble_keeps_local_model_fence_compatibility():
    text = "Tool call:\n```python\nprint('ok')\n```"

    blocks = parse_tool_blocks(text)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "python"
    assert blocks[0].content == "print('ok')"
    assert strip_tool_blocks(text) == ""
