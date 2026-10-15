"""A crew's ``enabled_tools`` allowlist must govern ``mcp__*`` tools too.

Why this exists
---------------
``_execute_llm_task`` used to invert the crew allowlist over
``BUILTIN_TOOL_DESCRIPTIONS`` alone. That dict contains no ``mcp__*`` names,
so the inversion was a no-op for MCP tools while the scheduler still
force-*included* the allowlisted ones. Net effect: a crew that exists
specifically to withhold the WhatsApp write tools was handed exactly those
tools — the RAG lane retrieved ``send_message`` semantically and nothing
took it back out. Confirmed on a live run's routing audit.

The fix inverts over the live MCP tool inventory too, but only when the
crew's allowlist actually names an ``mcp__*`` tool (opt-in by intent), so a
crew that never mentioned MCP keeps today's behaviour exactly.
"""

from __future__ import annotations

import json
import sys

from src.task_scheduler import crew_disabled_tools


class _Crew:
    def __init__(self, enabled_tools):
        self.enabled_tools = enabled_tools


def _fail(msg):
    print(f"  [FAIL] {msg}")
    sys.exit(1)


def _info(msg):
    print(f"  [ok] {msg}")


def test_crew_allowlist_withholds_unlisted_mcp_tools():
    """The analyzer's crew: five MCP reads allowed, the writes must go."""
    # Stand in for the live inventory: a bridge with reads and writes.
    import src.task_scheduler as ts

    live = {
        "mcp__oxs_whats2api_bridge__get_analysis_queue",
        "mcp__oxs_whats2api_bridge__list_messages",
        "mcp__oxs_whats2api_bridge__get_message",
        "mcp__oxs_whats2api_bridge__list_events",
        "mcp__oxs_whats2api_bridge__list_connections_local",
        "mcp__oxs_whats2api_bridge__send_message",
        "mcp__oxs_whats2api_bridge__call_method",
        "mcp__oxs_whats2api_bridge__send_media",
        "mcp__oxs_whats2api_bridge__mark_read",
        "mcp__oxs_whats2api_bridge__remove_connection",
        "mcp__email__list_emails",
    }
    original = ts._live_mcp_tool_names
    ts._live_mcp_tool_names = lambda: live
    try:
        crew = _Crew(json.dumps([
            "search_chats", "manage_notes", "manage_tasks",
            "manage_calendar", "manage_memory",
            "mcp__oxs_whats2api_bridge__get_analysis_queue",
            "mcp__oxs_whats2api_bridge__list_messages",
            "mcp__oxs_whats2api_bridge__get_message",
            "mcp__oxs_whats2api_bridge__list_events",
            "mcp__oxs_whats2api_bridge__list_connections_local",
        ]))
        disabled = crew_disabled_tools(crew)
    finally:
        ts._live_mcp_tool_names = original

    for write in (
        "mcp__oxs_whats2api_bridge__send_message",
        "mcp__oxs_whats2api_bridge__call_method",
        "mcp__oxs_whats2api_bridge__send_media",
        "mcp__oxs_whats2api_bridge__mark_read",
        "mcp__oxs_whats2api_bridge__remove_connection",
    ):
        if write not in disabled:
            _fail(f"allowlisted-MCP crew must withhold {write}")

    for allowed in (
        "mcp__oxs_whats2api_bridge__get_analysis_queue",
        "mcp__oxs_whats2api_bridge__list_messages",
    ):
        if allowed in disabled:
            _fail(f"crew must keep its own allowed tool {allowed}")

    _info("crew allowlist withholds every unlisted mcp__* tool")


def test_crews_without_mcp_names_are_untouched():
    """Opt-in by intent: a crew that never mentions MCP changes nothing.

    Otherwise every existing crew with a native-only allowlist would
    silently lose all of its MCP tools the first time a scheduled task ran.
    """
    import src.task_scheduler as ts

    calls = []

    def _boom():
        calls.append(1)
        raise AssertionError("MCP inventory must not even be consulted")

    original = ts._live_mcp_tool_names
    ts._live_mcp_tool_names = _boom
    try:
        disabled = crew_disabled_tools(_Crew(json.dumps(["search_chats", "bash"])))
    finally:
        ts._live_mcp_tool_names = original

    if calls:
        _fail("native-only crew must not trigger the MCP inversion")
    if any(n.startswith("mcp__") for n in disabled):
        _fail(f"native-only crew must not gain mcp__ denials: {disabled}")

    _info("a crew that names no mcp__ tool is left exactly as it was")


def test_missing_or_malformed_allowlist_is_a_no_op():
    import src.task_scheduler as ts
    original = ts._live_mcp_tool_names
    ts._live_mcp_tool_names = lambda: {"mcp__x__y"}
    try:
        for crew in (_Crew(None), _Crew(""), _Crew("not json"), _Crew("{}"),
                     _Crew(json.dumps([])), None):
            if crew_disabled_tools(crew):
                _fail(f"empty/broken allowlist must disable nothing: {crew!r}")
    finally:
        ts._live_mcp_tool_names = original
    _info("missing, empty or malformed allowlists disable nothing")


def test_unavailable_inventory_cannot_widen_access():
    """If the MCP inventory cannot be read, the inversion must no-op.

    Failing open here would hand back the exact behaviour this fixes, so
    the guard is: no inventory, no MCP denials.
    """
    import src.task_scheduler as ts
    original = ts._live_mcp_tool_names
    ts._live_mcp_tool_names = lambda: set()
    try:
        crew = _Crew(json.dumps([
            "mcp__oxs_whats2api_bridge__get_analysis_queue",
        ]))
        disabled = crew_disabled_tools(crew)
    finally:
        ts._live_mcp_tool_names = original
    if any(n.startswith("mcp__") for n in disabled):
        _fail(f"no inventory must mean no MCP denials: {disabled}")
    _info("an unreadable MCP inventory cannot widen access")


def test_disabled_set_actually_removes_them_from_relevant_tools():
    """End of the chain: the deny set reaches compose_task_relevant_tools.

    A deny set that never reaches the offered set is decoration.
    """
    from src.task_scheduler import compose_task_relevant_tools

    offered = compose_task_relevant_tools(
        rag_tools={
            "mcp__oxs_whats2api_bridge__send_message",
            "mcp__oxs_whats2api_bridge__get_analysis_queue",
        },
        assistant_always={"manage_memory"},
        disabled_tools={"mcp__oxs_whats2api_bridge__send_message"},
    )
    if "mcp__oxs_whats2api_bridge__send_message" in offered:
        _fail("a disabled mcp tool must not reach relevant_tools")
    if "mcp__oxs_whats2api_bridge__get_analysis_queue" not in offered:
        _fail("the allowed tool must survive to relevant_tools")
    _info("the deny set reaches relevant_tools, so the schema never shows it")


ALL_TESTS = [
    test_crew_allowlist_withholds_unlisted_mcp_tools,
    test_crews_without_mcp_names_are_untouched,
    test_missing_or_malformed_allowlist_is_a_no_op,
    test_unavailable_inventory_cannot_widen_access,
    test_disabled_set_actually_removes_them_from_relevant_tools,
]


def main() -> int:
    print("crew allowlist over mcp__* tools\n")
    for test in ALL_TESTS:
        test()
    print("\nAll crew-allowlist checks PASS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())