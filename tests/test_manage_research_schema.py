"""manage_research must be exposed to native function-calling models.

manage_research is dispatchable (TOOL_TAGS, _TOOL_NAME_MAP), executed by
do_manage_research, and RAG-indexed with an intent route ("open my research /
that report"), but it had no entry in FUNCTION_TOOL_SCHEMAS. So on
OpenAI/Anthropic native function-calling models the agent loop filters
FUNCTION_TOOL_SCHEMAS by name and never offers the tool — only the
fenced-text path (local models) could use it. Text models could manage
research; API models could not.
"""
import json

from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block


def test_manage_research_schema_present_and_well_formed():
    entry = next((s for s in FUNCTION_TOOL_SCHEMAS
                  if s.get("function", {}).get("name") == "manage_research"), None)
    assert entry is not None, "manage_research missing from FUNCTION_TOOL_SCHEMAS"
    params = entry["function"]["parameters"]
    assert params["properties"]["action"]["enum"] == ["list", "read", "delete"]
    assert params["required"] == ["action"]


def test_native_call_converts_to_dispatchable_block():
    blk = function_call_to_tool_block("manage_research", '{"action": "list"}')
    assert blk is not None
    assert blk.tool_type == "manage_research"
    assert json.loads(blk.content)["action"] == "list"
