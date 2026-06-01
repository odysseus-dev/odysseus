"""Tests for the mode-based tool permission pipeline (src/permissions.py).

Pure policy — no transport, no models. The point is that enforcement is the same
regardless of how a tool call arrived (native tool_calls vs a local model's
fenced block both produce a (tool, content) pair fed to evaluate()).
"""
import pytest

from src import permissions as perm


# -- classification ----------------------------------------------------------

@pytest.mark.parametrize("tool", ["web_search", "read_file", "list_emails", "read_email"])
def test_read_only_tools_classify_read(tool):
    assert perm.classify(tool) == perm.READ


@pytest.mark.parametrize("tool", ["create_document", "update_document", "edit_document"])
def test_edit_tools_classify_edit(tool):
    assert perm.classify(tool) == perm.EDIT


@pytest.mark.parametrize("tool", ["bash", "python", "send_email", "generate_image", "app_api", "write_file", "edit_file"])
def test_unknown_and_dangerous_default_to_mutate(tool):
    assert perm.classify(tool) == perm.MUTATE


def test_action_tool_read_verb_is_read():
    assert perm.classify("manage_tasks", '{"action": "list"}') == perm.READ
    assert perm.classify("manage_memory", '{"action": "search", "query": "x"}') == perm.READ


def test_action_tool_write_verb_is_mutate():
    assert perm.classify("manage_tasks", '{"action": "create", "title": "x"}') == perm.MUTATE
    assert perm.classify("manage_memory", '{"action": "delete", "id": 1}') == perm.MUTATE


def test_action_tool_no_or_bad_content_defaults_mutate():
    assert perm.classify("manage_settings", "") == perm.MUTATE
    assert perm.classify("manage_settings", "not json") == perm.MUTATE


# -- mode policy -------------------------------------------------------------

def test_plan_mode_allows_reads_denies_changes():
    assert perm.evaluate("plan", "read_file").action == perm.ALLOW
    assert perm.evaluate("plan", "write_file").action == perm.DENY
    assert perm.evaluate("plan", "bash").action == perm.DENY
    # a CRUD read slips through; a CRUD write does not
    assert perm.evaluate("plan", "manage_tasks", '{"action":"list"}').action == perm.ALLOW
    assert perm.evaluate("plan", "manage_tasks", '{"action":"create"}').action == perm.DENY


def test_manual_mode_approves_every_change():
    assert perm.evaluate("manual", "read_file").action == perm.ALLOW
    assert perm.evaluate("manual", "write_file").action == perm.APPROVE
    assert perm.evaluate("manual", "bash").action == perm.APPROVE


def test_accept_edits_auto_approves_edits_only():
    assert perm.evaluate("accept_edits", "create_document").action == perm.ALLOW
    assert perm.evaluate("accept_edits", "edit_document").action == perm.ALLOW
    assert perm.evaluate("accept_edits", "bash").action == perm.APPROVE
    assert perm.evaluate("accept_edits", "send_email").action == perm.APPROVE
    assert perm.evaluate("accept_edits", "write_file").action == perm.APPROVE  # arbitrary-path write prompts


def test_agent_mode_allows_everything():
    for tool in ["read_file", "write_file", "bash", "send_email", "app_api"]:
        assert perm.evaluate("agent", tool).action == perm.ALLOW


def test_chat_mode_denies_everything():
    for tool in ["read_file", "write_file", "bash"]:
        assert perm.evaluate("chat", tool).action == perm.DENY


def test_unknown_mode_falls_back_to_safe():
    # unknown mode -> 'manual' posture: mutations need approval, not auto-run
    assert perm.evaluate("bogus", "bash").action == perm.APPROVE
    assert perm.evaluate("bogus", "read_file").action == perm.ALLOW


def test_decision_helpers_and_reasons():
    d = perm.evaluate("manual", "bash")
    assert d.needs_approval and not d.allowed and "approval" in d.reason
    d2 = perm.evaluate("plan", "write_file")
    assert d2.action == perm.DENY and "read-only" in d2.reason
    assert perm.evaluate("agent", "read_file").allowed


def test_modes_order_is_rising_autonomy():
    # the composer cycles in this exact order; keep it stable
    assert perm.MODES == ("chat", "plan", "manual", "accept_edits", "agent")
