import json

import src.agent_tools  # noqa: F401
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS, function_call_to_tool_block


def _schema_for(name: str) -> dict:
    return next(s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == name)


def test_manage_research_is_exposed_to_native_function_calling():
    schema = _schema_for("manage_research")
    params = schema["function"]["parameters"]

    assert params["required"] == ["action"]
    assert {"list", "read", "open", "view", "get", "delete"}.issubset(
        set(params["properties"]["action"]["enum"])
    )
    assert "id" in params["properties"]
    assert "search" in params["properties"]


def test_manage_research_native_call_converts_to_tool_block():
    args = {"action": "read", "id": "abc123"}

    block = function_call_to_tool_block("manage_research", json.dumps(args))

    assert block is not None
    assert block.tool_type == "manage_research"
    assert json.loads(block.content) == args
