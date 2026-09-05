"""Regression coverage for the disabled agent-action approval gate."""

from src.tool_capabilities import ToolRunSecurityContext


def test_tainted_agent_action_does_not_require_approval_by_default():
    context = ToolRunSecurityContext(external_untrusted_context_seen=True)

    decision = context.decision_for("bash", "printf allowed")

    assert decision.allowed is True
    assert decision.reason is None
