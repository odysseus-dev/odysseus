"""Issue #2756 — a native web_search function call must preserve time_filter.

The web_search schema advertises a time_filter enum and the executor honors it
when content is JSON {"query","time_filter"}, but function_call_to_tool_block's
web_search branch emitted a bare query string and dropped time_filter. These pin
that a valid filter is passed through as JSON, while plain/invalid cases stay a
bare string (back-compat).
"""
import sys
from unittest.mock import MagicMock

# Clean up any mocks from previous tests to ensure we load real modules.
for mod in ['src.agent_tools', 'src.tool_parsing', 'src.tool_schemas', 'src.tool_execution']:
    sys.modules.pop(mod, None)

# Mock heavy database/model dependencies before importing (avoids the
# src.tool_schemas <-> src.agent_tools circular import pulling in the DB layer).
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'core.models', 'core.database', 'core.auth'
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import json  # noqa: E402

import src.agent_tools  # noqa: E402, F401
from src.tool_schemas import function_call_to_tool_block  # noqa: E402


def test_time_filter_is_preserved_as_json():
    block = function_call_to_tool_block(
        "web_search", json.dumps({"query": "openai pricing", "time_filter": "year"})
    )
    assert block is not None and block.tool_type == "web_search"
    parsed = json.loads(block.content)
    assert parsed["query"] == "openai pricing"
    assert parsed["time_filter"] == "year"


def test_plain_query_stays_bare_string():
    block = function_call_to_tool_block("web_search", json.dumps({"query": "openai pricing"}))
    assert block.content == "openai pricing"


def test_invalid_time_filter_falls_back_to_bare_query():
    block = function_call_to_tool_block(
        "web_search", json.dumps({"query": "openai pricing", "time_filter": "decade"})
    )
    assert block.content == "openai pricing"


def test_queries_list_shape_still_carries_filter():
    block = function_call_to_tool_block(
        "web_search", json.dumps({"queries": ["latest gpu prices"], "time_filter": "week"})
    )
    parsed = json.loads(block.content)
    assert parsed["query"] == "latest gpu prices"
    assert parsed["time_filter"] == "week"
