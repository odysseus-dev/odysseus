"""Issue #3229 and explicit web-toggle regressions.

Bug: allow_bash and allow_web_search were only read from form_data, so JSON
API callers (Content-Type: application/json) always had bash disabled.

Fix: (1) Read from JSON body as fallback.
     (2) Keep bash on the privilege fallback when unset.
     (3) Require an explicit per-turn web setting before exposing web tools.
"""

import ast
from pathlib import Path

from src.tool_policy import (
    WEB_TOOL_NAMES,
    explicit_tool_names_for_turn,
    is_web_search_explicitly_denied,
    web_search_enabled_for_turn,
)

_CHAT_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"
_AGENT_LOOP = Path(__file__).resolve().parent.parent / "src" / "agent_loop.py"


# ── Source-level guards ─────────────────────────────────────────


def test_allow_bash_reads_from_body_as_fallback():
    """chat_stream must read allow_bash from the JSON body, not just form_data."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)

    # Find the chat_stream function
    chat_stream_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream":
            chat_stream_func = node
            break
    assert chat_stream_func is not None, "chat_stream function not found"

    # Look for an assignment to allow_bash that references 'body'
    found_body_fallback = False
    for node in ast.walk(chat_stream_func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "allow_bash":
                    # Check if 'body' appears in the value
                    src_segment = ast.get_source_segment(source, node)
                    if src_segment and "body" in src_segment:
                        found_body_fallback = True
    assert found_body_fallback, (
        "allow_bash assignment in chat_stream must fall back to JSON body"
    )


def test_allow_web_search_reads_from_body_as_fallback():
    """chat_stream must read allow_web_search from the JSON body, not just form_data."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    tree = ast.parse(source)

    chat_stream_func = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "chat_stream":
            chat_stream_func = node
            break
    assert chat_stream_func is not None

    found_body_fallback = False
    for node in ast.walk(chat_stream_func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "allow_web_search":
                    src_segment = ast.get_source_segment(source, node)
                    if src_segment and "body" in src_segment:
                        found_body_fallback = True
    assert found_body_fallback, (
        "allow_web_search assignment in chat_stream must fall back to JSON body"
    )


def test_server_does_not_infer_run_mode_or_permissions_from_prompt_text():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "src.action_intents" not in source
    assert "chat→agent auto-escalation" not in source
    assert "_explicit_web_intent" not in source
    assert "_resolve_workspace_from_message_path" not in source


def test_agent_selection_does_not_turn_active_email_relevance_into_a_deny():
    source = _AGENT_LOOP.read_text(encoding="utf-8")

    assert "_active_email_draft_relevant" not in source
    assert "_is_email_document_obj" not in source


def test_agent_loop_expands_browser_mcp_tools_from_connected_server():
    """Browser intent must not depend on stale hardcoded Playwright tool names."""
    source = (Path(__file__).resolve().parent.parent / "src" / "agent_loop.py").read_text(encoding="utf-8")
    assert "def _expand_browser_mcp_tools" in source
    assert "server_id\") == \"builtin_browser\"" in source
    assert "_relevant_tools = _expand_browser_mcp_tools(_relevant_tools, mcp_mgr)" in source


def test_disabled_tools_respects_missing_vs_explicit_toggles():
    """Bash still defers to privileges, but web is an explicit per-turn opt-in.
    """
    source = _CHAT_ROUTES.read_text(encoding="utf-8")

    # The fix changes:
    #   if str(allow_bash).lower() != "true":
    # to:
    #   if allow_bash is not None and str(allow_bash).lower() != "true":
    assert "allow_bash is not None" in source, (
        "disabled_tools check must guard against allow_bash being None"
    )
    assert "web_search_enabled_for_turn(allow_web_search, use_web)" in source, (
        "web tools must be gated through the explicit per-turn web setting"
    )
    assert "disabled_tools.update(WEB_TOOL_NAMES)" in source, (
        "disabled_tools must add web_search/web_fetch when web is not explicitly enabled"
    )
    assert "explicit_tool_names_for_turn(" in source, (
        "explicit request controls must seed additive selection"
    )
    assert "explicit_tools=_explicit_tools" in source


def test_json_mode_reads_from_the_explicit_request_body():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert '(body or {}).get("mode")' in source


# ── Functional tests of the disabled-tools logic ───────────────


def _build_disabled_tools(
    allow_bash=None,
    allow_web_search=None,
    use_web=None,
    can_use_bash=True,
    can_use_browser=True,
    global_disabled=None,
):
    """Replicate the disabled-tools logic from chat_stream for unit testing.

    Returns the set of tool names that would be disabled.
    """
    disabled_tools = set()

    # Issue #3229 fix: only disable bash when explicitly set to a falsy value.
    if allow_bash is not None and str(allow_bash).lower() != "true":
        disabled_tools.add("bash")
    search_enabled = web_search_enabled_for_turn(allow_web_search, use_web)
    if is_web_search_explicitly_denied(allow_web_search) or not search_enabled:
        disabled_tools.update(WEB_TOOL_NAMES)
    if search_enabled:
        disabled_tools.difference_update(WEB_TOOL_NAMES)

    # Enforce per-user privileges
    if not can_use_bash:
        disabled_tools.update({"bash", "python", "read_file", "write_file"})
    if not can_use_browser:
        disabled_tools.add("builtin_browser")
    if global_disabled and isinstance(global_disabled, list):
        disabled_tools.update(global_disabled)

    return disabled_tools


def test_json_body_allow_bash_true_enables_bash():
    """API caller sending {"allow_bash": true} gets bash enabled."""
    disabled = _build_disabled_tools(allow_bash="true")
    assert "bash" not in disabled


def test_json_body_allow_bash_false_disables_bash():
    """API caller sending {"allow_bash": false} gets bash disabled."""
    disabled = _build_disabled_tools(allow_bash="false")
    assert "bash" in disabled


def test_json_body_allow_web_search_true_enables_web():
    """API caller sending {"allow_web_search": true} gets web tools enabled."""
    disabled = _build_disabled_tools(allow_web_search="true")
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_json_body_allow_web_search_false_disables_web():
    """API caller sending {"allow_web_search": false} gets web tools disabled."""
    disabled = _build_disabled_tools(allow_web_search="false")
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_chat_mode_use_web_true_enables_web():
    """Chat pre-search sends use_web=true as the explicit web setting."""
    disabled = _build_disabled_tools(use_web="true")
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_allow_web_search_false_wins_over_use_web_true():
    """The agent web toggle hard-denies web even if another path says use_web=true."""
    disabled = _build_disabled_tools(allow_web_search="false", use_web="true")
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_explicit_false_disables_web():
    disabled = _build_disabled_tools(
        allow_web_search="false",
    )
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_prompt_content_cannot_enable_web_without_setting():
    disabled = _build_disabled_tools(
        allow_web_search=None,
        use_web=None,
    )
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_admin_user_gets_bash_enabled_by_default():
    """When allow_bash is not set and user has can_use_bash privilege,
    bash must NOT be disabled.
    """
    disabled = _build_disabled_tools(allow_bash=None, can_use_bash=True)
    assert "bash" not in disabled


def test_web_search_disabled_by_default_without_explicit_turn_setting():
    """Missing web settings must not expose web tools by default."""
    disabled = _build_disabled_tools(allow_web_search=None)
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_non_privileged_user_without_explicit_flag_still_disabled():
    """A user without can_use_bash privilege who doesn't send allow_bash
    should still have bash disabled via the privilege check.
    """
    disabled = _build_disabled_tools(allow_bash=None, can_use_bash=False)
    assert "bash" in disabled


def test_non_privileged_user_explicit_true_overridden_by_privilege():
    """Even if allow_bash=true is sent, a user without can_use_bash
    privilege still gets bash disabled by the privilege gate.
    """
    disabled = _build_disabled_tools(allow_bash="true", can_use_bash=False)
    assert "bash" in disabled


def test_global_disabled_web_wins_over_explicit_web_enable():
    """Admin-level disabled tools are still a hard deny."""
    disabled = _build_disabled_tools(
        allow_web_search="true",
        global_disabled=["web_search", "web_fetch"],
    )
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_url_turn_keeps_bash_and_web_as_additive_explicit_tools():
    selected = explicit_tool_names_for_turn(
        allow_bash="true",
        allow_web_search="true",
    )

    assert selected == {"bash", "web_search", "web_fetch"}


def test_form_data_none_body_true_works():
    """A missing form value falls back to the JSON request body."""
    form_data_val = None
    body_val = "true"
    allow_bash = form_data_val if form_data_val is not None else body_val
    assert str(allow_bash).lower() == "true"

    disabled = _build_disabled_tools(allow_bash=allow_bash)
    assert "bash" not in disabled


def test_json_false_is_not_lost_by_request_fallback():
    form_data_val = None
    body_val = False

    allow_bash = form_data_val if form_data_val is not None else body_val

    assert allow_bash is False
    assert "bash" in _build_disabled_tools(allow_bash=allow_bash)


def test_explicit_false_disables_even_for_admin():
    """An admin who explicitly sends allow_bash=false should have bash disabled."""
    disabled = _build_disabled_tools(
        allow_bash="false", can_use_bash=True,
    )
    assert "bash" in disabled


# ── Frontend source-level guards ──────────────────────────────

_CHAT_JS = Path(__file__).resolve().parent.parent / "static" / "js" / "chat.js"


def test_frontend_always_sends_explicit_allow_bash():
    """chat.js must always send allow_bash (both true and false), not only on toggle ON."""
    source = _CHAT_JS.read_text(encoding="utf-8")
    # Must not only append 'true' — must also handle the false case
    assert "allow_bash', el('bash-toggle').checked ? 'true' : 'false'" in source or \
           "allow_bash', 'false'" in source, (
        "Frontend must send explicit allow_bash=false when toggle is off"
    )


def test_frontend_does_not_infer_mode_or_bash_permission_from_message_text():
    source = _CHAT_JS.read_text(encoding="utf-8")

    assert "workspaceAgentIntent" not in source
    assert "fd.set('allow_bash', 'true')" not in source


def test_frontend_sends_explicit_allow_web_search_false_in_agent_mode():
    """chat.js must send allow_web_search=false when web toggle is off in agent mode."""
    source = _CHAT_JS.read_text(encoding="utf-8")
    assert "fd.append('allow_web_search', el('web-toggle').checked ? 'true' : 'false')" in source, (
        "Frontend must send explicit allow_web_search=false in agent mode when toggle is off"
    )
