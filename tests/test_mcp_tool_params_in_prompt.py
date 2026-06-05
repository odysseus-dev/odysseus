"""Regression for issue #2509 — MCP tools must expose their input parameters.

``McpManager.get_tool_descriptions_for_prompt()`` previously emitted only
``- name: description`` per MCP tool, so agents (notably on the fenced-block
tool path used by Ollama models) never saw a tool's declared inputs and guessed
argument names from the description alone. ``get_all_tools()`` also dropped the
``input_schema`` entirely. These tests pin that the inputs now reach both
surfaces.
"""

from src.mcp_manager import McpManager


def _mgr_with_tool() -> McpManager:
    mgr = McpManager()
    mgr._tools = {
        "srv1": [
            {
                "name": "fetch_doc",
                "description": "Fetch a document by path.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "file path"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["path"],
                },
            }
        ]
    }
    mgr._connections = {"srv1": {"status": "connected", "name": "Files", "identity": ""}}
    return mgr


def test_get_all_tools_carries_input_schema():
    tools = _mgr_with_tool().get_all_tools()
    assert tools and tools[0]["input_schema"]["properties"]["path"]["type"] == "string"


def test_prompt_descriptions_surface_param_names_and_required():
    text = _mgr_with_tool().get_tool_descriptions_for_prompt()
    assert "mcp__srv1__fetch_doc" in text
    assert "path" in text and "limit" in text   # inputs are surfaced to the model
    assert "required" in text                   # required-ness is surfaced


def test_format_mcp_params_handles_no_params():
    from src.mcp_manager import _format_mcp_params

    assert _format_mcp_params({}) == ""
    assert _format_mcp_params(None) == ""
    assert _format_mcp_params({"type": "object", "properties": {}}) == ""


def test_format_mcp_params_marks_required_and_types():
    from src.mcp_manager import _format_mcp_params

    out = _format_mcp_params(
        {
            "type": "object",
            "properties": {"q": {"type": "string"}, "n": {"type": "integer"}},
            "required": ["q"],
        }
    )
    assert '"q": string (required)' in out
    assert '"n": integer' in out
    assert '"n": integer (required)' not in out  # optional param not marked required
