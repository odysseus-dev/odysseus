"""Tests for _strip_disabled_tool_rules — see issue: rules for disabled tools
stay in the system prompt after the tool itself has been filtered out."""

from src.agent_loop import _strip_disabled_tool_rules


RULES = (
    "## Base rules\n"
    "- Prefer native tool/function calling when tools are needed.\n"
    '- User identity facts/preferences ("my name is X") use `manage_memory`, not contacts.\n'
    "- Notes/todos/reminders use `manage_notes`, not memory.\n"
)


def test_drops_only_lines_naming_a_disabled_tool():
    out = _strip_disabled_tool_rules(RULES, {"manage_memory"})
    assert "`manage_memory`" not in out
    assert "`manage_notes`" in out
    assert "Prefer native tool/function calling" in out
    assert out.startswith("## Base rules")


def test_unrelated_disabled_tool_changes_nothing():
    assert _strip_disabled_tool_rules(RULES, {"web_search"}) == RULES


def test_no_op_without_disabled_tools():
    assert _strip_disabled_tool_rules(RULES, set()) == RULES
    assert _strip_disabled_tool_rules("", {"manage_memory"}) == ""


def test_prose_is_not_a_rule_line():
    text = "Memory lives in `manage_memory` for this deployment.\n- unrelated rule\n"
    assert _strip_disabled_tool_rules(text, {"manage_memory"}) == text


def test_indented_rule_lines_are_dropped():
    text = "## Rules\n  - nested rule using `manage_memory`\n  - keep this one\n"
    out = _strip_disabled_tool_rules(text, {"manage_memory"})
    assert "manage_memory" not in out
    assert "keep this one" in out


def test_multiple_disabled_tools():
    out = _strip_disabled_tool_rules(RULES, {"manage_memory", "manage_notes"})
    assert "manage_memory" not in out
    assert "manage_notes" not in out
    assert "Prefer native tool/function calling" in out
