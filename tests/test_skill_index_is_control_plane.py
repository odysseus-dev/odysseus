"""The chat preface's skill index does not block private reads.

chat_processor labels it "available skills index"; the agent loop labels the
same skill names and descriptions "skills". Only the latter was a
control-plane source, so in any agent chat with skills the gate treated the
run as tainted by external content and refused even listing the calendar.
Control-plane context still gates writes.
"""
import pytest

import src.tool_capabilities as tool_capabilities
from src.prompt_security import untrusted_context_message
from src.tool_capabilities import ToolRunSecurityContext


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(tool_capabilities, "TOOL_APPROVAL_GATE_ENABLED", True)
    context = ToolRunSecurityContext()
    context.observe_messages([
        untrusted_context_message("available skills index", "- weekly-review: summarise my week"),
        untrusted_context_message("backend runtime context", "containerized=true"),
    ])
    return context


def test_reads_pass_with_only_the_skill_index_and_runtime_context(gated):
    decision = gated.decision_for("manage_calendar", '{"action": "list_events"}')
    assert decision.allowed, decision.reason


def test_writes_still_need_approval(gated):
    decision = gated.decision_for("manage_notes", '{"action": "create", "title": "x", "content": "y"}')
    assert not decision.allowed
