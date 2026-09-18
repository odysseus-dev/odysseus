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


def test_explicit_mcp_server_reference_keeps_its_tools_visible():
    mgr = _mgr_with_tool()

    selected = mgr.get_tools_for_explicit_server_reference(
        "Use the Files MCP tool to fetch a document."
    )

    assert selected == {"mcp__srv1__fetch_doc"}


def test_explicit_mcp_server_reference_returns_a_bounded_relevant_set():
    mgr = McpManager()
    mgr._tools = {"nextcloud": [
        {"name": "webdav_list_directory", "description": "List directory contents."},
        {"name": "webdav_create_file", "description": "Create a file."},
    ]}
    mgr._connections = {"nextcloud": {"name": "Nextcloud MCP", "identity": ""}}

    selected = mgr.get_tools_for_explicit_server_reference(
        "Use the Nextcloud MCP to list my root folder contents. Read-only: do not create files.",
        max_tools=1,
    )

    assert selected == {"mcp__nextcloud__webdav_list_directory"}


def test_explicit_mcp_server_reference_respects_disabled_tools_and_names():
    mgr = _mgr_with_tool()

    disabled = mgr.get_tools_for_explicit_server_reference(
        "Use Files MCP", {"srv1": {"fetch_doc"}}
    )
    unrelated = mgr.get_tools_for_explicit_server_reference("Use Nextcloud MCP")

    assert disabled == set()
    assert unrelated == set()


def test_prompt_descriptions_surface_param_names_and_required():
    text = _mgr_with_tool().get_tool_descriptions_for_prompt()
    assert "mcp__srv1__fetch_doc" in text
    assert "path" in text and "limit" in text   # inputs are surfaced to the model
    assert "required" in text                   # required-ness is surfaced


def test_prompt_descriptions_only_include_selected_mcp_tools():
    mgr = _mgr_with_tool()
    mgr._tools["srv1"].append({"name": "delete_doc", "description": "Delete a document."})

    text = mgr.get_tool_descriptions_for_prompt(tool_names={"mcp__srv1__fetch_doc"})

    assert "mcp__srv1__fetch_doc" in text
    assert "mcp__srv1__delete_doc" not in text


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
