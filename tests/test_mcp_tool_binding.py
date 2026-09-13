"""Regression tests for the Penpot MCP tool-binding incident.

Root cause: ``_tool_schemas_for_round`` filtered EVERY MCP tool schema
through the RAG-selected ``relevant_tools`` set whenever that set was
non-empty. ``relevant_tools`` is a semantic-retrieval heuristic sized for
Odysseus's ~100 builtin tools; it was never a reliable gate for a small,
user-configured external MCP server. A connected server's tools (e.g.
Penpot's ``execute_code``) silently vanished from the schema on any turn
whose wording didn't happen to score well against the tool index --
including every "refreshed"/follow-up turn in a conversation, since RAG
retrieval reruns per turn from the message text alone. Only the always-
visible ``manage_mcp`` admin wrapper survived, so the model reported the
real tools "unavailable" even though the server was connected the whole
time.

Fix: MCP tool schemas are split into a small ``mcp_gated_names`` set (the
large embedded catalogs -- browser, GitHub, Todoist, ... -- that legitimately
need RAG/intent gating to avoid flooding a small model's schema list) and
everything else, which now binds unconditionally once a server is connected
and enabled, independent of retrieval, round number, or turn-to-turn state.
"""

import src.agent_loop as al
from src.mcp_manager import McpManager


def _mcp_schema(server_id: str, tool_name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": f"mcp__{server_id}__{tool_name}",
            "description": f"[MCP:{server_id}] {tool_name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


PENPOT_TOOLS = ["execute_code", "get_page", "list_boards", "export_asset", "set_layer"]
PENPOT_SCHEMAS = [_mcp_schema("penpot", name) for name in PENPOT_TOOLS]
BROWSER_SCHEMAS = [_mcp_schema("builtin_browser", name) for name in ("click", "navigate", "screenshot")]


def _round(relevant_tools, mcp_schemas, **overrides):
    kwargs = dict(
        force_answer=False,
        is_api_model=True,
        relevant_tools=relevant_tools,
        needs_admin=False,
        mcp_schemas=mcp_schemas,
        disabled_tools=set(),
        ody_qwen_finetune_model=False,
        last_user="",
    )
    kwargs.update(overrides)
    return al._tool_schemas_for_round(**kwargs)


def _names(schemas):
    return {s["function"]["name"] for s in schemas if s.get("function")}


def _mcp_names(schemas):
    return {n for n in _names(schemas) if n.startswith("mcp__")}


# ── 1. Connected MCP server tools appear in the turn tool schema ──────────


def test_connected_external_mcp_tools_survive_a_rag_miss():
    # The RAG retrieval for this turn's wording didn't surface anything from
    # Penpot -- a vague follow-up like "now run it" is exactly the case that
    # triggered the incident. The connected server's tools must still bind.
    relevant_tools = {"ask_user", "manage_memory"}  # ALWAYS_AVAILABLE-ish, no penpot hits
    mgr = McpManager()
    mgr._tools = {"penpot": [{"name": n, "description": n, "input_schema": {}} for n in PENPOT_TOOLS]}

    selected = _round(
        relevant_tools,
        PENPOT_SCHEMAS,
        mcp_gated_names=mgr.gated_tool_names(),
    )

    assert {"mcp__penpot__execute_code", "mcp__penpot__get_page"} <= _names(selected)


def test_admin_intent_no_longer_hides_the_real_tools_behind_manage_mcp():
    # Reproduces the exact incident shape: the query reads as admin/MCP-ish
    # ("manage my MCP servers" style wording), which pulls manage_mcp into
    # scope, while the actual per-tool schemas from the connected server must
    # ALSO still be present -- not just the management wrapper.
    relevant_tools = {"ask_user"}
    mgr = McpManager()
    mgr._tools = {"penpot": [{"name": n, "description": n, "input_schema": {}} for n in PENPOT_TOOLS]}

    selected = _round(
        relevant_tools,
        PENPOT_SCHEMAS,
        needs_admin=True,
        mcp_gated_names=mgr.gated_tool_names(),
    )

    names = _names(selected)
    assert "manage_mcp" in names  # the wrapper is still there
    assert "mcp__penpot__execute_code" in names  # ...and so is the real tool


def test_large_embedded_catalogs_still_require_retrieval_relevance():
    # Guard against reintroducing the ~30-schema Playwright flood: gated
    # (embedded, large) catalogs must still need a real RAG/intent hit.
    relevant_tools = {"ask_user"}
    mgr = McpManager()
    mgr._tools = {"builtin_browser": [{"name": n, "description": n, "input_schema": {}} for n in ("click", "navigate", "screenshot")]}

    selected = _round(
        relevant_tools,
        BROWSER_SCHEMAS,
        mcp_gated_names=mgr.gated_tool_names(),
    )

    assert _mcp_names(selected) == set()


def test_gated_tool_shows_once_actually_retrieved():
    relevant_tools = {"ask_user", "mcp__builtin_browser__click"}
    mgr = McpManager()
    mgr._tools = {"builtin_browser": [{"name": n, "description": n, "input_schema": {}} for n in ("click", "navigate", "screenshot")]}

    selected = _round(
        relevant_tools,
        BROWSER_SCHEMAS,
        mcp_gated_names=mgr.gated_tool_names(),
    )

    assert _mcp_names(selected) == {"mcp__builtin_browser__click"}


def test_disabled_external_tool_stays_hidden_even_though_unconditionally_bound():
    relevant_tools = {"ask_user"}
    mgr = McpManager()
    mgr._tools = {"penpot": [{"name": n, "description": n, "input_schema": {}} for n in PENPOT_TOOLS]}

    selected = _round(
        relevant_tools,
        PENPOT_SCHEMAS,
        disabled_tools={"mcp__penpot__execute_code"},
        mcp_gated_names=mgr.gated_tool_names(),
    )

    names = _names(selected)
    assert "mcp__penpot__execute_code" not in names
    assert "mcp__penpot__get_page" in names


# ── 2. Reconnect/refresh rebinds connected tools ───────────────────────────


def test_reconnect_rebinds_freshly_discovered_tools():
    mgr = McpManager()
    mgr._tools = {"penpot": [{"name": "execute_code", "description": "run", "input_schema": {}}]}
    mgr._connections = {"penpot": {"status": "connected", "name": "Penpot", "identity": ""}}

    first = _names(mgr.get_all_openai_schemas())
    assert first == {"mcp__penpot__execute_code"}

    # Simulate a reconnect that (re)discovers a fuller tool list -- e.g. the
    # server was restarted after an update, or the very first connect raced
    # ahead of a slow handshake and only partially listed tools.
    mgr._tools["penpot"] = [
        {"name": n, "description": n, "input_schema": {}} for n in PENPOT_TOOLS
    ]
    second = _names(mgr.get_all_openai_schemas())
    assert second == {f"mcp__penpot__{n}" for n in PENPOT_TOOLS}

    # And the new tools are immediately eligible for unconditional binding --
    # not gated -- exactly like the original set was.
    assert mgr.gated_tool_names() == set()


def test_refreshed_turn_recomputes_schemas_from_live_manager_state():
    # A "refreshed" turn calls get_all_openai_schemas() again from scratch
    # (see _build_system_prompt); it must reflect whatever the manager
    # currently knows, not a stale snapshot from a previous turn.
    mgr = McpManager()
    mgr._tools = {}
    mgr._connections = {}
    assert mgr.get_all_openai_schemas() == []

    mgr._tools = {"penpot": [{"name": "execute_code", "description": "run", "input_schema": {}}]}
    mgr._connections = {"penpot": {"status": "connected", "name": "Penpot", "identity": ""}}
    assert _names(mgr.get_all_openai_schemas()) == {"mcp__penpot__execute_code"}


# ── 3. Stream interruption does not lose pending tool availability ────────


def test_tool_availability_is_stable_across_rounds_regardless_of_retry_state():
    # The agent loop recomputes the schema list every round (see
    # stream_agent_loop's two _tool_schemas_for_round call sites). Binding
    # must not depend on how many rounds already ran, whether a previous
    # round's stream was interrupted/retried, or whether the missing-tool
    # self-unblock ever fired -- it was a safety net for the old bug, not a
    # requirement for correct behavior.
    mgr = McpManager()
    mgr._tools = {"penpot": [{"name": n, "description": n, "input_schema": {}} for n in PENPOT_TOOLS]}
    gated = mgr.gated_tool_names()

    # Round 1: narrow relevant_tools, as if this is the very first round.
    round_1 = _names(_round({"ask_user"}, PENPOT_SCHEMAS, mcp_gated_names=gated))
    # Round 2: a DIFFERENT (still narrow, still missing penpot) relevant_tools
    # set, as if a mid-stream interruption forced a retry that recomputed
    # retrieval independently and still didn't retrieve the right thing.
    round_2 = _names(_round({"manage_memory"}, PENPOT_SCHEMAS, mcp_gated_names=gated))

    expected = {f"mcp__penpot__{n}" for n in PENPOT_TOOLS}
    assert expected <= round_1
    assert expected <= round_2  # not lost on the second round either


def test_mcp_mgr_none_yields_no_mcp_schemas_without_raising():
    # A dropped/None mcp_mgr (e.g. plan-mode disable, public endpoint scoping)
    # must degrade to "no MCP tools this turn", never a crash that would cut
    # the stream short.
    selected = _round({"ask_user"}, [], mcp_gated_names=None)
    assert _mcp_names(selected) == set()
