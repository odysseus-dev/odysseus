"""Local text models can leak web_search calls as prose plus bare JSON.

gpt-oss-20b sometimes writes:

    Need to do web_search for ...
    {"query":"...", "time_filter":"week"}

That is an intended tool call in non-native/textual tool mode, but older parsing
only recognized fenced blocks, [TOOL_CALL], XML invoke, and tool_code markup.
"""
import json
import sys
from unittest.mock import MagicMock

for mod in ['src.agent_tools', 'src.tool_parsing', 'src.tool_schemas', 'src.tool_execution']:
    sys.modules.pop(mod, None)
for mod in [
    'sqlalchemy', 'sqlalchemy.orm', 'sqlalchemy.ext', 'sqlalchemy.ext.declarative',
    'sqlalchemy.ext.hybrid', 'sqlalchemy.sql', 'sqlalchemy.sql.expression',
    'src.database', 'core.models', 'core.database', 'core.auth'
]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

import src.agent_tools  # noqa: E402, F401
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks  # noqa: E402


def test_raw_json_after_web_search_phrase_runs_as_web_search():
    text = (
        "Need to do web_search for best chocolate chip cookies. Use web_search function.\n\n"
        '{"query":"best chocolate chip cookie recipe","time_filter":"week"}'
    )

    blocks = parse_tool_blocks(text)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    payload = json.loads(blocks[0].content)
    assert payload == {
        "query": "best chocolate chip cookie recipe",
        "time_filter": "week",
    }


def test_raw_json_without_web_tool_name_is_ignored():
    text = 'Here is a saved search config:\n\n{"query":"private customer name"}'

    assert parse_tool_blocks(text) == []


def test_raw_json_fallback_is_disabled_for_native_parser_gate():
    text = (
        "Need to do web_search for best chocolate chip cookies.\n\n"
        '{"query":"best chocolate chip cookie recipe"}'
    )

    assert parse_tool_blocks(text, skip_fenced=True) == []


def test_strip_tool_blocks_removes_executed_raw_json():
    text = (
        "Need to do web_search for best chocolate chip cookies. Use web_search function.\n\n"
        '{"query":"best chocolate chip cookie recipe","time_filter":"week"}'
    )

    cleaned = strip_tool_blocks(text)

    assert '{"query"' not in cleaned
    assert "best chocolate chip cookie recipe" not in cleaned
    assert "Need to do web_search" in cleaned
