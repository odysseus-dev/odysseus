"""Regression for issue #1789 — an installed, connected MCP server's tools are not
callable in chat/agent ("no MCP server with that name or tool installed"), even
though they load on the integrations tab.

Root cause: for an API/function-calling model (local Ollama models count — they're
served over the OpenAI-compatible API, _is_api_model=True), agent_loop filtered the
MCP tool schemas by the RAG-selected `relevant_tools` set, same as builtins. So a
connected MCP tool was dropped whenever the user's message didn't semantically
match the tool's description, and the model never received it.

MCP tools are user-installed integrations — a small, deliberate set — not part of
the large builtin catalog the RAG trims. _select_api_tool_schemas now ALWAYS
appends the MCP schemas while still trimming builtins by relevance.
"""

from src.agent_loop import _select_api_tool_schemas, _ADMIN_SCHEMA_NAMES


def _fn(name):
    return {"type": "function", "function": {"name": name}}


MCP = [_fn("mcp__weather__forecast"), _fn("mcp__notion__search")]


def test_mcp_schemas_included_even_when_not_rag_selected():
    """The #1789 case: relevant_tools (from RAG) contains only builtins, none of
    the MCP tool names — the MCP schemas must STILL be offered."""
    builtins = [_fn("web_search"), _fn("web_fetch"), _fn("read_file")]
    relevant = {"web_search"}  # RAG picked a builtin, no MCP names
    out = [s["function"]["name"] for s in _select_api_tool_schemas(builtins, MCP, relevant, False)]
    assert "mcp__weather__forecast" in out and "mcp__notion__search" in out, \
        "connected MCP tools must be offered regardless of RAG relevance (#1789)"
    # builtins are still trimmed by relevance.
    assert "web_search" in out
    assert "web_fetch" not in out and "read_file" not in out


def test_builtins_still_rag_filtered():
    builtins = [_fn("web_search"), _fn("bash"), _fn("python")]
    out = [s["function"]["name"] for s in _select_api_tool_schemas(builtins, [], {"bash"}, False)]
    assert out == ["bash"]


def test_no_rag_set_admin_gating():
    builtins = [_fn("web_search"), _fn("manage_tokens")]  # manage_tokens is admin
    # non-admin: admin tools dropped, MCP still appended
    out = [s["function"]["name"] for s in _select_api_tool_schemas(builtins, MCP, None, False)]
    assert "manage_tokens" not in out and "web_search" in out
    assert "mcp__weather__forecast" in out
    # admin: all builtins kept
    out2 = [s["function"]["name"] for s in _select_api_tool_schemas(builtins, MCP, None, True)]
    assert "manage_tokens" in out2 and "mcp__notion__search" in out2


def test_admin_name_set_is_sane():
    assert "manage_tokens" in _ADMIN_SCHEMA_NAMES  # guards the gating test's premise
