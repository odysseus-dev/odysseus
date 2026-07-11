"""Tests for src/agent/recovery.py"""
from __future__ import annotations
from src.agent.recovery import (
    RecoveryPrompts,
    IntentSupervisor,
    RecoveryLevel,
)


def test_mild_text_recovery_prompt():
    msg = RecoveryPrompts.text_loop(RecoveryLevel.MILD)
    assert isinstance(msg, str)
    assert len(msg) > 50
    assert "think" in msg.lower() or "different" in msg.lower() or "varied" in msg.lower()


def test_strong_text_recovery_prompt():
    msg = RecoveryPrompts.text_loop(RecoveryLevel.STRONG)
    assert isinstance(msg, str)
    assert len(msg) > 50
    assert "stop" in msg.lower() or "answer" in msg.lower() or "converge" in msg.lower()


def test_stall_recovery_prompt():
    msg = RecoveryPrompts.stall(RecoveryLevel.STRONG)
    assert isinstance(msg, str)
    assert "tool" in msg.lower() or "repeat" in msg.lower()


def test_runaway_recovery_prompt():
    msg = RecoveryPrompts.runaway("bash")
    assert isinstance(msg, str)
    assert "bash" in msg.lower() or "repeating" in msg.lower()


def test_force_answer_prompt():
    msg = RecoveryPrompts.force_answer()
    assert isinstance(msg, str)
    assert "stop" in msg.lower() or "answer" in msg.lower() or "prose" in msg.lower()


def test_force_answer_with_disabled_tools():
    msg = RecoveryPrompts.force_answer(disabled_tools=["web_search", "bash"])
    assert "web_search" in msg.lower() or "bash" in msg.lower()


def test_intent_supervisor_detects_action_phrases():
    sup = IntentSupervisor(max_nudges=2)
    assert sup.detect("Let me check the logs") is True
    assert sup.detect("I'll investigate the issue") is True
    assert sup.detect("We need to run the command") is True


def test_intent_supervisor_ignores_harmless_text():
    sup = IntentSupervisor(max_nudges=2)
    assert sup.detect("Let me know what you think") is False
    assert sup.detect("Here is the result") is False
    assert sup.detect("I found the answer") is False


def test_intent_supervisor_capped():
    sup = IntentSupervisor(max_nudges=2)
    assert sup.should_nudge() is True
    sup.nudge()
    assert sup.should_nudge() is True
    sup.nudge()
    assert sup.should_nudge() is False


def test_intent_supervisor_reset():
    sup = IntentSupervisor(max_nudges=2)
    sup.nudge()
    sup.nudge()
    assert sup.should_nudge() is False
    sup.reset()
    assert sup.should_nudge() is True
