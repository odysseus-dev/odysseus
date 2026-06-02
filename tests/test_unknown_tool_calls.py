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
import src.agent_tools
from src.tool_parsing import parse_tool_blocks
from src.tool_schemas import function_call_to_tool_block
from src.tool_execution import execute_tool_block
from types import SimpleNamespace


def test_parse_xml_unknown_tool_returns_none():
    """XML-style <invoke> tags with truly unknown tools should be filtered out (return None)."""
    text = '<invoke name="super_secret_tool"><parameter name="arg1">value1</parameter></invoke>'
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 0


def test_parse_tool_call_unknown_tool_returns_none():
    """[TOOL_CALL] blocks with truly unknown tools should be filtered out (return None)."""
    text = '[TOOL_CALL] {tool => "mega_blast", command => "run energy"} [/TOOL_CALL]'
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 0


def test_function_call_to_tool_block_unknown_tool_returns_none():
    """Native function calls of truly unknown tools should return None."""
    block = function_call_to_tool_block("ultra_zap", '{"power": 9000}')
    assert block is None


def test_function_call_to_tool_block_invalid_json_returns_none():
    """Unparseable JSON arguments should result in returning None."""
    block = function_call_to_tool_block("web_search", '{"query": "valid json')  # invalid JSON
    assert block is None


def test_google_search_mapping():
    """google_search should map to web_search and extract the first query from queries list or string."""
    import json

    # List of queries case
    block = function_call_to_tool_block("google_search", '{"queries": ["testing google search"]}')
    assert block is not None
    assert block.tool_type == "web_search"
    payload = json.loads(block.content)
    assert payload["query"] == "testing google search"
    assert payload["count"] == 5

    # Single string query case
    block = function_call_to_tool_block("google_search_retrieval", '{"queries": "testing google search string"}')
    assert block is not None
    assert block.tool_type == "web_search"
    payload = json.loads(block.content)
    assert payload["query"] == "testing google search string"
    assert payload["count"] == 5


def test_web_search_query_only_produces_json_payload():
    """web_search with only query produces JSON with default count, no time_filter."""
    import json
    block = function_call_to_tool_block("web_search", '{"query": "test query"}')
    assert block is not None
    assert block.tool_type == "web_search"
    payload = json.loads(block.content)
    assert payload["query"] == "test query"
    assert payload["count"] == 5
    assert "time_filter" not in payload


def test_web_search_time_filter_included_when_present():
    """web_search with time_filter includes it in the JSON payload."""
    import json
    block = function_call_to_tool_block(
        "web_search",
        '{"query": "latest news", "time_filter": "day"}'
    )
    assert block is not None
    payload = json.loads(block.content)
    assert payload["query"] == "latest news"
    assert payload["time_filter"] == "day"


def test_web_search_count_is_passed_through():
    """web_search count parameter is passed through to JSON payload."""
    import json
    block = function_call_to_tool_block(
        "web_search",
        '{"query": "test", "count": 10}'
    )
    payload = json.loads(block.content)
    assert payload["count"] == 10


def test_web_search_queries_list_takes_priority():
    """When both query and queries are present, queries[0] wins."""
    import json
    block = function_call_to_tool_block(
        "web_search",
        '{"query": "ignored", "queries": ["from list"]}'
    )
    payload = json.loads(block.content)
    assert payload["query"] == "from list"
