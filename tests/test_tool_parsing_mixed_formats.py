import warnings

import pytest

import src.agent_tools  # noqa: F401  # initialize the tool registry first

from src.tool_parsing import (
    _parse_python_like_content,
    parse_tool_blocks,
    strip_tool_blocks,
)


def test_parenthesized_prose_is_not_treated_as_a_function_call(caplog):
    text = "Applied physics (verified), 1080x1440 px. Example & Co. (final)."

    assert parse_tool_blocks(text) == []
    assert "Unknown function call" not in caplog.text


def test_python_like_fallback_does_not_emit_shell_escape_warnings():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(SyntaxError):
            _parse_python_like_content('grep -i "alpha\\|beta" file.txt')

    assert not [item for item in caught if issubclass(item.category, SyntaxWarning)]


def test_qwen_native_function_parameter_markup_parses_first_call():
    blocks = parse_tool_blocks(
        "<tool_call>\n"
        "<function=manage_notes>\n"
        "<parameter=action>\n"
        "search\n"
        "</parameter>\n"
        "<parameter=query>\n"
        "Integration checklist\n"
        "</parameter>\n"
        "</function>\n"
        "</tool_call>",
        skip_fenced=True,
    )

    assert [(block.tool_type, block.content) for block in blocks] == [
        ("manage_notes", '{"action": "search", "query": "Integration checklist"}')
    ]


def test_qwen_native_email_numeric_args_are_coerced():
    blocks = parse_tool_blocks(
        "<tool_call>\n"
        "<function=mcp__email__search_emails>\n"
        "<parameter=query>\n"
        "BackupBox warning update\n"
        "</parameter>\n"
        "<parameter=max_results>\n"
        "10\n"
        "</parameter>\n"
        "</function>\n"
        "</tool_call>",
        skip_fenced=True,
    )

    assert [(block.tool_type, block.content) for block in blocks] == [
        ("mcp__email__search_emails", '{"query": "BackupBox warning update", "max_results": 10}')
    ]


def test_tool_parser_is_directly_importable_without_facade_cycle():
    """The low-level parser is a supported import surface."""
    blocks = parse_tool_blocks(
        '<｜｜DSML｜｜tool_calls>'
        '<｜｜DSML｜｜invoke name="host_shell">'
        '<｜｜DSML｜｜parameter name="command" string="true">ip route'
        '</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke>'
        '</｜｜DSML｜｜tool_calls>',
        skip_fenced=True,
    )
    assert [(block.tool_type, block.content) for block in blocks] == [
        ("host_shell", '{"command": "ip route"}')
    ]


def test_explicit_call_wins_over_an_illustrative_fence():
    text = (
        "Example:\n"
        "```bash\n"
        "echo example\n"
        "```\n"
        "Now execute:\n"
        '[TOOL_CALL]{tool => "shell", args => {--command "printf real"}}[/TOOL_CALL]'
    )

    blocks = parse_tool_blocks(text)

    assert [(block.tool_type, block.content) for block in blocks] == [
        ("bash", "printf real")
    ]


def test_mixed_parse_and_strip_leave_the_nonexecuted_fence_visible():
    text = (
        "Example:\n"
        "```bash\n"
        "echo example\n"
        "```\n"
        '[TOOL_CALL]{tool => "shell", args => {--command "printf real"}}[/TOOL_CALL]'
    )

    cleaned = strip_tool_blocks(text)

    assert "echo example" in cleaned
    assert "[TOOL_CALL]" not in cleaned
    assert "printf real" not in cleaned


def test_strip_qwen_open_tools_envelope_from_visible_text():
    text = (
        '<|open|>tools<|sep|><|open|>call tool="web_search" index="1"<|sep|>'
        '<|open|>argument key="query" type="string"<|sep|>'
        '"M5 Ultra Mac Studio unified memory"<|close|>argument<|sep|>'
        '<|close|>call<|sep|><|close|>tools<|sep|><|close|>message<|sep|>'
    )

    assert strip_tool_blocks(text) == ""
