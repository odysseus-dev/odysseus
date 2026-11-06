"""Issue #5527 — "web search off" must also close the deep-research route.

With the composer's web toggle off, web_search/web_fetch are denied for the turn
but trigger_research was not, so "search online for X" still reached the internet
— through a multi-round research job that crawls far more than the single search
the user had just switched off.

The carve-out matters as much as the denial: routes/chat_routes.py gates the
user's own Deep Research request on `tool_policy.blocks("trigger_research")`, so
denying that name unconditionally would silently break the Deep Research toggle
(which forces chat mode, where no web toggle is ever sent).
"""

from pathlib import Path

from src.tool_policy import (
    WEB_TOOL_NAMES,
    build_effective_tool_policy,
    tools_denied_without_web,
)

_CHAT_ROUTES = Path(__file__).resolve().parent.parent / "routes" / "chat_routes.py"


def test_agent_cannot_start_research_when_web_is_denied():
    denied = tools_denied_without_web(research_requested=False)
    assert "trigger_research" in denied
    assert WEB_TOOL_NAMES <= denied


def test_explicit_deep_research_request_still_runs_with_web_denied():
    """The Deep Research toggle is its own opt-in; the turn it sends never
    carries a web toggle, so denying research here would disable the feature."""
    assert "trigger_research" not in tools_denied_without_web(research_requested=True)


def test_reading_saved_research_is_never_denied():
    """manage_research is Library CRUD over already-stored reports — no web."""
    for requested in (True, False):
        assert "manage_research" not in tools_denied_without_web(research_requested=requested)


def test_denied_names_reach_the_gate_the_research_path_reads():
    """chat_routes derives research_blocked_by_policy from exactly this call."""
    policy = build_effective_tool_policy(
        disabled_tools=tools_denied_without_web(research_requested=False),
        last_user_message="search online for beans",
    )
    assert policy.blocks("trigger_research")
    assert not policy.blocks("manage_research")


def test_chat_route_applies_the_rule_on_the_web_denied_branch():
    source = _CHAT_ROUTES.read_text(encoding="utf-8")
    assert "tools_denied_without_web(research_requested=do_research)" in source, (
        "the web-denied branch must deny research too, or the toggle is advisory"
    )
