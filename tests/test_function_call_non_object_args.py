import sys
from unittest.mock import MagicMock

# Clean up any mocks from previous tests to ensure we load real modules
for mod in ['src.agent_tools', 'src.tool_parsing', 'src.tool_schemas', 'src.tool_execution']:
    sys.modules.pop(mod, None)

# Mock heavy database/model dependencies before importing
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


@pytest.mark.parametrize("arguments", [
    '["ls -la"]',   # JSON array
    '"ls -la"',     # bare JSON string
    '42',            # JSON number
    'true',          # JSON bool
    'null',          # JSON null
])
def test_non_object_arguments_do_not_crash(arguments):
    """A native function call whose arguments are valid JSON but not an object
    must not raise (it used to throw AttributeError: 'list' object has no
    attribute 'get', aborting the entire agent stream)."""
    block = function_call_to_tool_block("bash", arguments)
    # Coerced to empty args -> empty bash command, but importantly NO crash.
    assert block is not None
    assert block.tool_type == "bash"
    assert block.content == ""


def test_edit_document_skips_non_object_edit_items():
    block = function_call_to_tool_block(
        "edit_document",
        '{"edits": ["bad", 42, null, {"find": "old", "replace": "new"}]}',
    )

    assert block is not None
    assert block.tool_type == "edit_document"
    assert block.content == "<<<FIND>>>\nold\n<<<REPLACE>>>\nnew\n<<<END>>>"


def test_suggest_document_skips_non_object_suggestion_items():
    block = function_call_to_tool_block(
        "suggest_document",
        '{"suggestions": ["bad", 42, null, {"find": "old", "replace": "new", "reason": "clearer"}]}',
    )

    assert block is not None
    assert block.tool_type == "suggest_document"
    assert block.content == (
        "<<<FIND>>>\nold\n<<<SUGGEST>>>\nnew\n<<<REASON>>>\nclearer\n<<<END>>>"
    )
