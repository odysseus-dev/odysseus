"""Issue #3229 and explicit web-toggle regressions.

Bug: allow_bash and allow_web_search were only read from form_data, so JSON
API callers (Content-Type: application/json) always had bash disabled.

Fix: (1) Read from JSON body as fallback.
     (2) Keep bash on the privilege fallback when unset.
     (3) Require an explicit per-turn web setting before exposing web tools.
"""

import ast
import json
from pathlib import Path

import pytest

from src.action_intents import classify_tool_intent
from routes.chat_routes import _is_personal_data_search_without_web_target
from routes.chat_routes import _explicitly_denies_web_lookup
from routes.chat_routes import _contains_explicit_url_target
from routes.chat_routes import _authorizes_exact_url_fetch
from routes.chat_routes import _is_explicit_browser_automation_request
from routes.chat_routes import _is_external_discovery_request
from routes.chat_routes import _prefers_structured_document_tools
from routes.chat_routes import (
    _has_recent_private_browser_success,
    _is_contextual_browser_followup,
)
from routes.chat_routes import _most_recent_successful_web_tool
from routes.chat_routes import _is_contextual_browser_followup
from routes.chat_routes import _is_contextual_web_followup
from src.tool_policy import (
    WEB_ACCESS_TOOL_NAMES,
    WEB_TOOL_NAMES,
    is_web_search_explicitly_denied,
    web_intent_may_enable_for_turn,
    web_search_enabled_for_turn,
)
from tests.test_foreground_model_routing import _RouteRequest, _chat_stream_endpoint

_CHAT_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"


def test_personal_data_search_is_not_mistaken_for_web_search():
    assert _is_personal_data_search_without_web_target(
        "Search saved memory for the Harbor Guji delay."
    )
    assert _is_personal_data_search_without_web_target(
        "Find the prior chat where we fixed the parser."
    )
    assert _is_personal_data_search_without_web_target(
        "Search my previous chats for the Odysseus TUI parser fix."
    )
    assert _is_personal_data_search_without_web_target(
        "Do I have any events today?"
    )
    assert not _is_personal_data_search_without_web_target(
        "Search saved memory, then check the latest news online."
    )


def test_workspace_artifact_paths_are_not_mistaken_for_public_urls():
    assert not _contains_explicit_url_target("Save /workspace/output.md")
    assert not _contains_explicit_url_target("Inspect /workspace/fixtures/video.mp4")
    assert _contains_explicit_url_target("Open https://example.com/report")
    assert _contains_explicit_url_target("Open example.com/report")


def test_plain_pdf_url_is_retrieval_not_browser_automation():
    prompt = "Download and read https://arxiv.org/pdf/2311.08526"

    assert _contains_explicit_url_target(prompt)
    assert not _is_explicit_browser_automation_request(prompt)
    assert _is_explicit_browser_automation_request(
        "Open the page https://example.com/report and click the details link"
    )
    assert _is_explicit_browser_automation_request(
        "Open https://example.com with the private browesr"
    )


def test_authoritative_source_discovery_is_web_intent():
    assert _is_external_discovery_request(
        "Find the official announcement and tell me the date."
    )
    assert _is_external_discovery_request("Locate the press release")
    assert not _is_external_discovery_request("Find my announcement note")


def test_exact_public_url_authorizes_fetch_without_broad_search():
    assert _authorizes_exact_url_fetch(
        "Summarize https://example.com/reports/quarterly"
    )
    assert not _authorizes_exact_url_fetch(
        "Open https://example.com and click the details link"
    )
    assert not _authorizes_exact_url_fetch(
        "Summarize https://youtu.be/example"
    )
    assert not _authorizes_exact_url_fetch(
        "Do not search or fetch https://example.com/private"
    )


def test_agent_loop_treats_fetch_only_contract_as_web_capable():
    source = (Path(__file__).resolve().parent.parent / "src" / "agent_loop.py").read_text(encoding="utf-8")
    assert 'and not turn_contract.permits("web_fetch")' in source


def test_external_paper_tables_prefer_structured_tools_over_shell():
    assert _prefers_structured_document_tools(
        'From the paper "Example Suite", merge Table 2 and Table 4.'
    )
    assert _prefers_structured_document_tools(
        "Download and read https://arxiv.org/pdf/2311.08526"
    )
    assert not _prefers_structured_document_tools(
        "Extract Table 2 from /workspace/fixtures/paper.pdf"
    )


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


def test_personal_store_search_takes_precedence_over_generic_web_words():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")

    assert "_explicit_personal_store_intent" in source
    assert "_explicit_web_target" in source
    assert "not _explicit_personal_store_intent or _explicit_web_target" in source
    assert "and not _explicit_personal_store_intent" in source


def test_browser_form_followups_include_approval_and_send_phrases():
    """Short approval replies after a form/browser turn must keep browser tools available."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "approved" in source
    assert "proceed" in source
    assert "send(?:\\s+it)?" in source
    assert "submit(?:\\s+it)?" in source


def test_agent_loop_expands_browser_mcp_tools_from_connected_server():
    """Browser intent must not depend on stale hardcoded Playwright tool names."""
    source = (Path(__file__).resolve().parent.parent / "src" / "agent_loop.py").read_text(encoding="utf-8")
    assert "def _expand_browser_mcp_tools" in source
    assert "server_id\") == \"builtin_browser\"" in source
    assert "_relevant_tools = _expand_browser_mcp_tools(_relevant_tools, mcp_mgr, disabled_tools)" in source


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
    assert "web_intent_may_enable_for_turn(" in source, (
        "prompt web intent must respect both caller and message-level denials"
    )
    agent_source = (Path(__file__).resolve().parent.parent / "src" / "agent_loop.py").read_text(encoding="utf-8")
    assert "explicit web domain enabled private web tools" in agent_source
    assert "elif forced_tools and (set(forced_tools) & WEB_TOOL_NAMES):" in agent_source
    assert "disabled_tools.difference_update(WEB_TOOL_NAMES)" in agent_source
    assert "disabled_tools.update(WEB_TOOL_NAMES)" in source, (
        "disabled_tools must add web_search/web_fetch when web is not explicitly enabled"
    )
    assert "_forced_tools = set(WEB_TOOL_NAMES)" in source, (
        "web tools should only be forced visible from the explicit web setting"
    )


def test_explicit_private_browser_workflow_survives_web_search_disabled():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "_explicit_private_browser_intent" in source
    assert "_explicit_private_browser_intent = bool(re.search" in source
    assert "if not (_explicit_browser_intent or _local_browser_render_intent):" in source
    assert 'disabled_tools.add("private_browser")' in source


def test_clean_private_browser_warmth_requires_typed_success():
    successful = type("Session", (), {"history": [{
        "role": "assistant",
        "metadata": {"tool_events": [{
            "tool": "private_browser", "exit_code": 0, "error": False,
        }]},
    }]})()
    failed = type("Session", (), {"history": [{
        "role": "assistant",
        "metadata": {"tool_events": [{
            "tool": "private_browser", "exit_code": 1, "error": True,
        }]},
    }]})()
    prose_only = type("Session", (), {"history": [{
        "role": "assistant", "content": "I used private_browser",
    }]})()

    assert _has_recent_private_browser_success(successful)
    assert not _has_recent_private_browser_success(failed)
    assert not _has_recent_private_browser_success(prose_only)


def test_retry_on_that_page_is_a_contextual_browser_followup():
    session = type("Session", (), {"history": [
        {"role": "user", "content": "Go to example.com in the browser."},
        {"role": "assistant", "content": "Opened the page."},
    ]})()

    assert _is_contextual_browser_followup(
        "Try again on that page and compare the prices.", session,
    )


def test_contextual_browser_followup_recognizes_current_page_inspection():
    session = type("Session", (), {"history": [{
        "role": "user",
        "content": "Browse https://example.com and take a snapshot.",
    }]})()

    assert _is_contextual_browser_followup(
        "What heading is visible on that page? Check the current page before answering.",
        session,
    )
    assert not _is_contextual_browser_followup("Show my notes.", session)


async def _clean_route_contract(monkeypatch, message, *, history=(), native=False):
    from routes import chat_routes
    from src import tool_security

    with monkeypatch.context() as route_patch:
        endpoint = _chat_stream_endpoint(
            route_patch, "agent", {},
            session_model="odysseus-qwen3.5-tools-pre-heretic",
            session_history=history,
        )
        route_patch.setattr(
            chat_routes, "coerce_message_and_session",
            lambda *args, **kwargs: (message, "session-1"),
        )
        route_patch.setattr(
            tool_security, "owner_is_admin_or_single_user", lambda owner: True,
        )
        observed = []

        async def capture_agent(*args, **kwargs):
            observed.append(kwargs["turn_contract"])
            yield 'data: {"delta":"Contract constructed."}\n\n'
            yield "data: [DONE]\n\n"

        route_patch.setattr(chat_routes, "stream_agent_loop", capture_agent)
        request = _RouteRequest("agent")
        request._form.update({"message": message, "compare_mode": "false"})
        if native:
            request._form.update({
                "cwd": "/tmp/native-workspace",
                "workspace": "/tmp/native-workspace",
                "client_runtime_context": json.dumps({
                    "surface": "odysseus-native", "terminal_agent": True,
                    "unattended_mode": True,
                    "input_files": ["/workspace/paper.pdf"],
                }),
            })
        response = await endpoint(request)
        async for _ in response.body_iterator:
            pass
        assert len(observed) == 1
        return observed[0]


@pytest.mark.asyncio
async def test_clean_browser_filter_preserves_native_pdf_extraction_contract(monkeypatch):
    contract = await _clean_route_contract(
        monkeypatch,
        "Extract Table 2 from /workspace/paper.pdf using pdf_extract.",
        native=True,
    )
    assert contract.capabilities == {"shell_files"}
    assert contract.permits("pdf_extract")
    assert "private_browser" not in contract.offered
    assert not {"manage_tasks", "search_emails", "send_email"} & contract.offered


@pytest.mark.asyncio
async def test_clean_preview_only_offers_browser_for_explicit_or_typed_warm_turns(monkeypatch):
    message = "Summarize the status."
    no_history = await _clean_route_contract(monkeypatch, message)
    failed_history = [{"role": "assistant", "metadata": {"tool_events": [{
        "tool": "private_browser", "exit_code": 1, "error": True,
    }]}}]
    failed = await _clean_route_contract(monkeypatch, message, history=failed_history)
    successful_history = [{"role": "assistant", "metadata": {"tool_events": [{
        "tool": "private_browser", "exit_code": 0, "error": False,
    }]}}]
    warm = await _clean_route_contract(monkeypatch, message, history=successful_history)
    explicit = await _clean_route_contract(
        monkeypatch, "Open https://example.com with the private browser",
    )
    assert "private_browser" not in no_history.offered
    assert "private_browser" not in failed.offered
    assert warm.permits("private_browser")
    assert explicit.permits("private_browser")


@pytest.mark.asyncio
async def test_explicit_web_fetch_is_not_erased_by_generic_browser_intent(monkeypatch):
    contract = await _clean_route_contract(
        monkeypatch,
        "Use web_fetch to read https://example.com/report in the private browser.",
    )
    assert contract.capabilities == {"search_browser"}
    assert contract.required == {"web_fetch"}
    assert contract.permits("web_fetch")
    assert not {"manage_tasks", "search_emails", "send_email"} & contract.offered


def test_web_followup_grammar_covers_article_detail_questions():
    from routes.chat_routes import _WEB_FOLLOWUP_RE

    assert _WEB_FOLLOWUP_RE.fullmatch("What else did it say about Miro?")
    assert _WEB_FOLLOWUP_RE.fullmatch("What did it say about pricing?")
    assert _WEB_FOLLOWUP_RE.fullmatch("grab the top story and read it")
    assert _WEB_FOLLOWUP_RE.fullmatch(
        "now pull the page title and last-updated date off that link"
    )


def test_contextual_web_followup_recognizes_referential_result_open():
    session = type("Session", (), {"history": [{
        "role": "user",
        "content": "look up what's happening in germany rn",
    }]})()

    assert _is_contextual_web_followup("grab the top story and read it", session)


def test_web_followup_retains_only_latest_successful_public_web_tool():
    session = type("Session", (), {"history": [
        {"role": "assistant", "metadata": {"tool_events": [
            {"tool": "web_search", "exit_code": 0},
            {"tool": "web_fetch", "exit_code": 0},
        ]}},
    ]})()
    assert _most_recent_successful_web_tool(session) == "web_fetch"


def test_failed_web_tool_is_not_retained_for_followup():
    session = type("Session", (), {"history": [
        {"role": "assistant", "metadata": {"tool_events": [
            {"tool": "web_fetch", "exit_code": 1, "error": True},
        ]}},
    ]})()
    assert _most_recent_successful_web_tool(session) is None


def test_site_navigation_forces_private_browser_with_search_enabled():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "visit|go\\s+to|navigate\\s+to" in source
    assert 'set(_BROWSER_MCP_TOOLS) | {"private_browser"}' in source


def test_web_toggle_preserves_typed_calendar_and_notes_tools():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")

    assert '"calendar": {"manage_calendar"}' in source
    assert '"notes": {"manage_notes", "manage_tasks"}' in source
    assert '_forced_tools.update(_typed_forced_tools)' in source


def test_local_html_render_is_separate_from_open_web_access():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "_local_media_needs_browser_render(message)" in source
    assert "_native_runtime_requires_local_browser(client_runtime_context)" in source
    assert "or _local_browser_render_intent" in source


def test_workspace_auto_escalation_keeps_shell_tools():
    """Workspace/shell auto-routing must not use the light typed-tool clamp."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert '_tool_intent.category in {"shell", "workspace"}' in source
    assert '_native_workspace_contract = bool(' in source
    assert "allow_bash = \"true\"" in source
    assert '_forced_tools.update({"bash", "ls", "manage_bg_jobs"})' in source
    assert "if auto_escalated and not _workspace_agent_intent and not _use_turn_contract:" in source


def test_native_declared_input_files_establish_workspace_intent():
    from routes.chat_routes import _native_context_has_workspace_inputs

    assert _native_context_has_workspace_inputs({
        "surface": "odysseus-native",
        "terminal_agent": True,
        "input_files": ["/workspace/fixtures/video.mp4"],
    })
    assert not _native_context_has_workspace_inputs({
        "surface": "odysseus-native",
        "terminal_agent": False,
        "input_files": ["/workspace/fixtures/video.mp4"],
    })


def test_chat_tool_privileges_use_effective_bearer_owner():
    """Bearer TUI/WebUI turns must not be denied as the synthetic ``api`` user."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    privilege_gate = source[source.index("# Enforce per-user privileges"):]
    assert "_user = effective_user(request)" in privilege_gate
    assert "_user = ctx.user" not in privilege_gate


# ── Functional tests of the disabled-tools logic ───────────────


def _build_disabled_tools(
    allow_bash=None,
    allow_web_search=None,
    use_web=None,
    can_use_bash=True,
    can_use_browser=True,
    explicit_web_intent=False,
    global_disabled=None,
    allowed_tools=None,
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
        disabled_tools.update(WEB_ACCESS_TOOL_NAMES)
    if explicit_web_intent:
        disabled_tools.update({
            "bash", "python",
            "search_chats", "manage_skills", "manage_memory",
            "read_file", "write_file", "edit_file",
            "create_document", "edit_document", "update_document",
            "send_email", "reply_to_email",
            "manage_notes", "manage_calendar", "manage_tasks",
            "api_call", "builtin_browser",
        })
        if search_enabled:
            disabled_tools.difference_update(WEB_TOOL_NAMES)
        else:
            disabled_tools.update(WEB_TOOL_NAMES)
    elif search_enabled:
        disabled_tools.difference_update(WEB_TOOL_NAMES)

    # Enforce per-user privileges
    if not can_use_bash:
        disabled_tools.update({"bash", "python", "read_file", "write_file"})
    if not can_use_browser:
        disabled_tools.add("builtin_browser")
    if global_disabled and isinstance(global_disabled, list):
        disabled_tools.update(global_disabled)
    if allowed_tools:
        known = {"manage_notes", "manage_documents", "manage_calendar", "manage_tasks", "manage_memory", "search_chats", "read_file", "bash", "ui_control", "app_api", "list_emails", "read_email"}
        allowed = set(allowed_tools)
        disabled_tools.update(known - allowed)
        disabled_tools.difference_update(allowed)

    return disabled_tools


def test_allowed_tools_allowlist_disables_off_list_tools():
    disabled = _build_disabled_tools(
        allow_bash="true",
        allowed_tools=["manage_notes", "ask_user"],
    )
    assert "manage_notes" not in disabled
    assert "bash" in disabled
    assert "ui_control" in disabled
    assert "manage_documents" in disabled


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
    assert "private_browser" in disabled
    assert "youtube_tool" in disabled


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


@pytest.mark.parametrize("denial", [False, "false", "0", "off"])
def test_explicit_web_toggle_denial_cannot_be_overridden_by_prompt_intent(denial):
    assert web_intent_may_enable_for_turn(denial) is False


def test_missing_web_toggle_can_be_inferred_from_prompt_intent():
    assert web_intent_may_enable_for_turn(None) is True
    assert web_intent_may_enable_for_turn(
        None,
        message_denies_lookup=True,
    ) is False


@pytest.mark.parametrize(
    "message",
    [
        "please use web search for current CVEs",
        "search the web for current CVEs",
        "can you look up the latest docs",
        "look this up and answer with sources",
    ],
)
def test_explicit_false_disables_web_despite_prompt_web_intent(message):
    """Explicit allow_web_search=false is a hard deny even when the prompt
    asks for web search."""
    intent = classify_tool_intent(message)
    assert intent is not None
    assert intent.category == "web"

    disabled = _build_disabled_tools(
        allow_web_search="false",
        explicit_web_intent=True,
    )
    assert "web_search" in disabled
    assert "web_fetch" in disabled


def test_trained_odysseus_route_uses_explicit_web_intent_without_ui_toggle_gate():
    """The exact trained model owns intent routing; other models retain the gate."""
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "_clean_v3_web_intent = bool(" in source
    assert "_clean_v3_route_requested" in source
    assert 'and "search_browser" in _turn_capabilities' in source
    assert "or _clean_v3_web_intent" in source
    assert "or _contextual_web_turn_followup or _clean_v3_web_intent" in source
    assert "and not _explicitly_denies_web_lookup(message)" in source


def test_browser_intent_is_promoted_to_search_browser_capability():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "if _use_turn_contract and _explicit_browser_intent:" in source
    assert '_turn_capabilities = _turn_capabilities | {"search_browser"}' in source


def test_prompt_web_intent_enables_web_without_frontend_toggle():
    """Explicit search/web wording should expose private web tools in agent mode."""
    intent = classify_tool_intent("look this up and answer with sources")
    assert intent is not None
    assert intent.category == "web"

    disabled = _build_disabled_tools(
        allow_web_search="true",
        use_web=None,
        explicit_web_intent=True,
    )
    assert "web_search" not in disabled
    assert "web_fetch" not in disabled


def test_explicit_no_search_phrase_blocks_web_auto_enable():
    assert _explicitly_denies_web_lookup("answer from memory only, do not search")


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


def test_form_data_none_body_true_works():
    """Simulates: form_data has no allow_bash, body has allow_bash=true.
    After the fallback (`form_data.get(...) or body.get(...)`), allow_bash
    should be "true".
    """
    # Simulate the fallback logic
    form_data_val = None  # not in form_data
    body_val = "true"     # from JSON body
    allow_bash = form_data_val or body_val
    assert str(allow_bash).lower() == "true"

    disabled = _build_disabled_tools(allow_bash=allow_bash)
    assert "bash" not in disabled


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


def test_frontend_generic_web_prose_does_not_grant_shell_authority():
    source = _CHAT_JS.read_text(encoding="utf-8")
    workspace_line = next(
        line for line in source.splitlines()
        if "const workspaceAgentIntent" in line
    )
    for generic in ("source", "system", "app", "change", "review", "server", "api"):
        assert generic not in workspace_line, generic
    assert "_explicitWorkspaceTarget" in workspace_line


def test_frontend_sends_explicit_allow_web_search_false_in_agent_mode():
    """chat.js must send allow_web_search=false when web toggle is off in agent mode."""
    source = _CHAT_JS.read_text(encoding="utf-8")
    assert "fd.append('allow_web_search', el('web-toggle').checked ? 'true' : 'false')" in source, (
        "Frontend must send explicit allow_web_search=false in agent mode when toggle is off"
    )
