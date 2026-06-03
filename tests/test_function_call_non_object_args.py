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


@pytest.mark.parametrize("name,key", [
    ("edit_document", "edits"),
    ("suggest_document", "suggestions"),
])
def test_non_dict_list_items_do_not_crash(name, key):
    """edit_document/suggest_document iterate a list and call item.get(...) on
    each entry. A model that emits a list of non-objects (e.g. ["bad", 42, null])
    used to raise AttributeError ('str'/'int'/'NoneType' has no attribute 'get'),
    aborting the agent stream. Non-dict items must be skipped instead."""
    import json
    block = function_call_to_tool_block(name, json.dumps({key: ["bad", 42, None]}))
    assert block is not None
    # Every item was non-dict and skipped -> no blocks emitted.
    assert block.content == ""


def test_edit_document_keeps_valid_items_and_skips_bad():
    """A mix of malformed and valid edits keeps the valid ones."""
    import json
    block = function_call_to_tool_block(
        "edit_document",
        json.dumps({"edits": ["bad", {"find": "a", "replace": "b"}, None]}),
    )
    assert block is not None
    assert block.content == "<<<FIND>>>\na\n<<<REPLACE>>>\nb\n<<<END>>>"
