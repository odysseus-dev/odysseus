"""Regression for #6709: when tool-RAG times out, the keyword fallback must
keep tools from a connected user MCP server that the request names."""

from src.agent_loop import _mcp_tools_named_in_query


class _FakeMgr:
    def __init__(self):
        self._tools = {
            "e6d3b43e": ("Obsidian", ["read_text_file", "list_directory"]),
            "a1b2": ("Notion", ["search"]),
            "memory": ("memory", ["recall"]),
        }

    def is_builtin(self, sid):
        return sid in {"memory", "rag", "email"} or sid.startswith("builtin_")

    def get_all_tools(self, disabled_map=None):
        out = []
        for sid, (name, tools) in self._tools.items():
            for t in tools:
                out.append({
                    "server_id": sid,
                    "server_name": name,
                    "name": t,
                    "qualified_name": f"mcp__{sid}__{t}",
                    "is_disabled": t in (disabled_map or {}).get(sid, set()),
                })
        return out


def test_named_server_tools_are_kept():
    q = "What does my About Me note in the Obsidian vault say about me? Use the Obsidian tools."
    assert _mcp_tools_named_in_query(q, _FakeMgr()) == {
        "mcp__e6d3b43e__read_text_file",
        "mcp__e6d3b43e__list_directory",
    }


def test_unnamed_servers_and_builtins_are_not_added():
    assert _mcp_tools_named_in_query("what is in my memory about notes", _FakeMgr()) == set()


def test_name_must_match_on_word_boundary():
    assert _mcp_tools_named_in_query("notionally speaking", _FakeMgr()) == set()


def test_disabled_tools_stay_excluded():
    got = _mcp_tools_named_in_query(
        "use obsidian", _FakeMgr(), {"e6d3b43e": {"list_directory"}}
    )
    assert got == {"mcp__e6d3b43e__read_text_file"}


def test_no_manager_is_safe():
    assert _mcp_tools_named_in_query("use obsidian", None) == set()
