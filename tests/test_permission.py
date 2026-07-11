"""Tests for src/agent/permission.py"""
from __future__ import annotations
from src.agent.permission import (
    Action,
    Rule,
    Ruleset,
    evaluate,
    merge_rulesets,
    disabled_tools,
    FORCED_ASK,
    AGENT_PERMISSIONS,
)


def test_rule_creation():
    rule = Rule(permission="bash", pattern="*", action=Action.ALLOW)
    assert rule.permission == "bash"
    assert rule.action == Action.ALLOW


def test_evaluate_allow():
    rules = [Rule(permission="bash", pattern="*", action=Action.ALLOW)]
    result = evaluate("bash", "ls -la", rules)
    assert result.action == Action.ALLOW


def test_evaluate_deny():
    rules = [Rule(permission="bash", pattern="*", action=Action.DENY)]
    result = evaluate("bash", "ls -la", rules)
    assert result.action == Action.DENY


def test_evaluate_last_wins():
    rules = [
        Rule(permission="bash", pattern="*", action=Action.ALLOW),
        Rule(permission="bash", pattern="rm *", action=Action.DENY),
    ]
    result = evaluate("bash", "rm -rf /", rules)
    assert result.action == Action.DENY


def test_evaluate_default_ask():
    rules = []
    result = evaluate("unknown_tool", "something", rules)
    assert result.action == Action.ASK


def test_evaluate_wildcard_permission():
    rules = [Rule(permission="*", pattern="*", action=Action.ALLOW)]
    result = evaluate("any_tool", "any_args", rules)
    assert result.action == Action.ALLOW


def test_evaluate_pattern_match():
    rules = [Rule(permission="bash", pattern="rm *", action=Action.DENY)]
    result = evaluate("bash", "ls -la", rules)
    assert result.action == Action.ALLOW
    result2 = evaluate("bash", "rm -rf /", rules)
    assert result2.action == Action.DENY


def test_merge_rulesets():
    base = [Rule(permission="bash", pattern="*", action=Action.ALLOW)]
    override = [Rule(permission="bash", pattern="rm *", action=Action.DENY)]
    merged = merge_rulesets(base, override)
    assert len(merged) == 2
    result = evaluate("bash", "rm -rf /", merged)
    assert result.action == Action.DENY


def test_disabled_tools():
    rules = [
        Rule(permission="bash", pattern="*", action=Action.DENY),
        Rule(permission="web_search", pattern="*", action=Action.ALLOW),
    ]
    disabled = disabled_tools(["bash", "web_search", "read_file"], rules)
    assert "bash" in disabled
    assert "web_search" not in disabled
    assert "read_file" not in disabled


def test_forced_ask_contains_bash_delete():
    assert "bash_delete" in FORCED_ASK


def test_agent_permissions_exist():
    assert "build" in AGENT_PERMISSIONS
    assert "plan" in AGENT_PERMISSIONS
    assert "explore" in AGENT_PERMISSIONS


def test_plan_mode_disables_writes():
    plan_rules = AGENT_PERMISSIONS["plan"]
    result = evaluate("edit_file", "src/main.py", plan_rules)
    assert result.action == Action.DENY


def test_explore_mode_limited():
    explore_rules = AGENT_PERMISSIONS["explore"]
    result = evaluate("write_file", "test.py", explore_rules)
    assert result.action == Action.DENY
    result2 = evaluate("read_file", "test.py", explore_rules)
    assert result2.action == Action.ALLOW
