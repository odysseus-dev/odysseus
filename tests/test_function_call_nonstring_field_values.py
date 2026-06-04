"""function_call_to_tool_block must not crash on non-string scalar field values.

`function_call_to_tool_block` already coerces non-object arguments to `{}`
(test_function_call_non_object_args) and #2168 hardened the edit_document /
suggest_document list branches against non-dict items (AttributeError). The
remaining gap is the *text-format* branches that concatenate field values:
`args.get("content", "") + "\n" + ...`. A model emitting a valid JSON object
with a wrongly-typed field (e.g. `write_file {"content": 42}`) made those
branches raise `TypeError: can only concatenate str (not "int") to str`, which
escaped the un-try/excepted call site in the agent loop and aborted the whole
turn mid-stream.

These pin the str-coercion so a mistyped field degrades gracefully instead of
killing the agent stream.
"""
import sys
from unittest.mock import MagicMock

# Clean up any mocks from previous tests to ensure we load real modules.
for mod in ['src.agent_tools', 'src.tool_parsing', 'src.tool_schemas', 'src.tool_execution']:
    sys.modules.pop(mod, None)

# Mock heavy database/model dependencies before importing.
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'core.models', 'core.database', 'core.auth'
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import pytest
import src.agent_tools  # noqa: F401
from src.tool_schemas import function_call_to_tool_block


@pytest.mark.parametrize("name, arguments, expected_substr", [
    ("write_file", '{"path": "/tmp/x", "content": 42}', "42"),
    ("chat_with_model", '{"model": "gpt", "message": 7}', "7"),
    ("create_session", '{"name": "X", "model": 123}', "123"),
    ("send_to_session", '{"session_id": "a", "message": 7}', "7"),
    ("ask_teacher", '{"model": "auto", "problem": 99}', "99"),
    ("create_document", '{"title": "t", "content": 5}', "5"),
    ("manage_memory", '{"action": "add", "text": 12345}', "12345"),
    ("manage_session", '{"action": "rename", "session_id": "s", "value": 9}', "9"),
])
def test_nonstring_field_values_do_not_crash(name, arguments, expected_substr):
    """A native call with a numeric field must convert (not raise TypeError)."""
    block = function_call_to_tool_block(name, arguments)
    assert block is not None
    # The numeric value is stringified into the tool content, not dropped.
    assert expected_substr in block.content


def test_string_field_values_unchanged():
    """The normal all-string path must be byte-for-byte unchanged."""
    block = function_call_to_tool_block("write_file", '{"path": "/tmp/x", "content": "hello"}')
    assert block.content == "/tmp/x\nhello"
