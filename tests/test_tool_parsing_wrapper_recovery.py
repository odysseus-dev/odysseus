"""Wrapper-recovery regressions for Qwen/Hermes text-mode tool calls.

Issues: #6014 (a malformed wrapper suppressed later valid bare calls),
#6013 (a closer token inside a JSON string value ended the wrapper span
early), #6012 (non-string command/code payloads were coerced instead of
rejected). Wrapper markers are built via concatenation so this file never
embeds a raw wrapper sequence that scanners could trip over.
"""
import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks
from src.tool_schemas import function_call_to_tool_block

OPEN = "<" + "tool_call>"
CLOSE = "</" + "tool_call" + ">"


def test_6014_malformed_wrapper_then_bare_invoke_parses():
    text = (
        OPEN + '{"name": "write_file", "arguments": {broken json' + CLOSE + "\n"
        "Now run this:\n"
        '<invoke name="bash"><parameter name="command">echo hi</parameter></invoke>'
    )
    blocks = parse_tool_blocks(text)
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "echo hi")]


def test_6013_closer_inside_json_string_value():
    payload = (
        '{"name": "write_file", "arguments": '
        '{"path": "n.txt", "content": "hello ' + CLOSE + ' world"}}'
    )
    text = OPEN + payload + CLOSE
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "write_file"
    assert ("hello " + CLOSE + " world") in blocks[0].content


def test_6012_nonstring_command_rejected():
    for bad in ('["ls", "-la"]', '{"cmd": "ls"}', "1"):
        text = OPEN + '{"name": "bash", "arguments": {"command": ' + bad + "}}" + CLOSE
        assert parse_tool_blocks(text) == [], bad
    assert function_call_to_tool_block("bash", '{"command": ["ls"]}') is None


def test_6012_nonstring_python_code_rejected():
    text = OPEN + '{"name": "python", "arguments": {"code": [1, 2]}}' + CLOSE
    assert parse_tool_blocks(text) == []
    assert function_call_to_tool_block("python", '{"code": {"x": 1}}') is None


def test_5333_markup_inside_malformed_json_stays_data():
    text = (
        OPEN + '{"name": "write_file", "arguments": {broken '
        '<invoke name="bash"><parameter name="command">echo unsafe</parameter></invoke>'
    )
    assert parse_tool_blocks(text) == []


def test_valid_json_wrapper_still_parses():
    text = OPEN + '{"name": "bash", "arguments": {"command": "ls"}}' + CLOSE
    blocks = parse_tool_blocks(text)
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "ls")]


def test_two_wrappers_with_malformed_first_recover():
    text = (
        OPEN + '{"name": "bash", "arguments": {broken' + CLOSE + "\n"
        + OPEN + '{"name": "bash", "arguments": {"command": "pwd"}}' + CLOSE
    )
    blocks = parse_tool_blocks(text)
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "pwd")]