from src.agent_run_policy import AgentRunPolicy, AuthorizationOutcome
from src.tool_capabilities import ToolRunSecurityContext


def test_inert_mcp_placeholder_does_not_widen_qualified_mcp_authority():
    policy = AgentRunPolicy.for_mode("sandbox")
    context = ToolRunSecurityContext(external_untrusted_context_seen=True)

    assert policy.authorize("mcp", context).outcome is AuthorizationOutcome.ALLOW_SANDBOXED
    assert (
        policy.authorize("mcp__unknown__surprise", context).outcome
        is AuthorizationOutcome.REQUIRE_APPROVAL
    )
