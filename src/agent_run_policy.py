"""Deterministic authority policy for one agent run.

The selected model may request actions, but it cannot choose the authority used
to execute them.  A server-owned run mode and tool capability metadata produce
one of three outcomes: execute in the workspace sandbox, execute with the
application user's host permissions, or require an exact user approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import src.tool_capabilities as tool_capabilities
from src.tool_capabilities import (
    POST_EXTERNAL_BLOCKED_EFFECTS,
    ToolCapabilities,
    ToolEffect,
    ToolRunSecurityContext,
    capabilities_for_tool,
)


class AgentRunMode(str, Enum):
    ASK = "ask"
    SANDBOX = "sandbox"
    FULL_ACCESS = "full_access"


class ApprovalPolicy(str, Enum):
    ALWAYS_FOR_RISK = "always_for_risk"
    ON_TRUST_BOUNDARY = "on_trust_boundary"
    NEVER = "never"


class ExecutionProfile(str, Enum):
    WORKSPACE_SANDBOX = "workspace_sandbox"
    HOST_FULL_ACCESS = "host_full_access"


class NetworkProfile(str, Enum):
    BROKERED_ONLY = "brokered_only"
    OPEN = "open"


class AuthorizationOutcome(str, Enum):
    ALLOW_SANDBOXED = "allow_sandboxed"
    ALLOW_HOST = "allow_host"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"


@dataclass(frozen=True)
class ToolAuthorization:
    outcome: AuthorizationOutcome
    reason: str | None = None
    capabilities: ToolCapabilities | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome in {
            AuthorizationOutcome.ALLOW_SANDBOXED,
            AuthorizationOutcome.ALLOW_HOST,
        }


_ASK_RISK_EFFECTS = frozenset(
    {
        ToolEffect.READ_PRIVATE,
        ToolEffect.WRITE_WORKSPACE,
        ToolEffect.WRITE_PRIVATE,
        ToolEffect.EXECUTE_CODE,
        ToolEffect.NETWORK_EGRESS,
        ToolEffect.EXTERNAL_SIDE_EFFECT,
        ToolEffect.UI_SIDE_EFFECT,
        ToolEffect.ADMIN_CHANGE,
        ToolEffect.DESTRUCTIVE,
    }
)

_SANDBOX_ALWAYS_APPROVE_EFFECTS = frozenset(
    {
        ToolEffect.NETWORK_EGRESS,
        ToolEffect.EXTERNAL_SIDE_EFFECT,
        ToolEffect.ADMIN_CHANGE,
        ToolEffect.DESTRUCTIVE,
    }
)

# `mcp` is an inert legacy parser/instrumentation placeholder. The dispatcher
# has no generic MCP execution branch; real MCP calls use qualified
# `mcp__server__tool` names and are classified separately. Letting this exact
# sentinel reach dispatch preserves compatibility without authorizing an
# unknown qualified MCP capability.
_INERT_COMPATIBILITY_TOOL_NAMES = frozenset({"mcp"})


def _sandbox_approval_effects(capabilities: ToolCapabilities) -> frozenset[ToolEffect]:
    """Return effects that still cross Sandbox's contained authority boundary."""
    approval_effects = capabilities.effects & _SANDBOX_ALWAYS_APPROVE_EFFECTS
    if ToolEffect.BROKERED_NETWORK_READ in capabilities.effects:
        # Brokered public reads remain constrained by the server's URL and
        # network policy. Treating their implementation egress as arbitrary
        # egress would turn ordinary web/RAG flows into approval loops.
        approval_effects -= {ToolEffect.NETWORK_EGRESS}
    return approval_effects


def parse_agent_run_mode(value: Any) -> AgentRunMode:
    """Parse a client/database value, failing safely to the sandbox default."""
    if isinstance(value, AgentRunMode):
        return value
    try:
        return AgentRunMode(str(value or "").strip().lower())
    except ValueError:
        return AgentRunMode.SANDBOX


@dataclass(frozen=True)
class AgentRunPolicy:
    mode: AgentRunMode
    approval_policy: ApprovalPolicy
    execution_profile: ExecutionProfile
    network_profile: NetworkProfile

    @classmethod
    def for_mode(cls, value: Any) -> "AgentRunPolicy":
        mode = parse_agent_run_mode(value)
        if mode is AgentRunMode.ASK:
            return cls(
                mode=mode,
                approval_policy=ApprovalPolicy.ALWAYS_FOR_RISK,
                execution_profile=ExecutionProfile.WORKSPACE_SANDBOX,
                network_profile=NetworkProfile.BROKERED_ONLY,
            )
        if mode is AgentRunMode.FULL_ACCESS:
            return cls(
                mode=mode,
                approval_policy=ApprovalPolicy.NEVER,
                execution_profile=ExecutionProfile.HOST_FULL_ACCESS,
                network_profile=NetworkProfile.OPEN,
            )
        return cls(
            mode=AgentRunMode.SANDBOX,
            approval_policy=ApprovalPolicy.ON_TRUST_BOUNDARY,
            execution_profile=ExecutionProfile.WORKSPACE_SANDBOX,
            network_profile=NetworkProfile.BROKERED_ONLY,
        )

    def authorize(
        self,
        tool_name: Any,
        security_context: ToolRunSecurityContext,
    ) -> ToolAuthorization:
        """Classify an action without consulting model-generated text."""
        capabilities = capabilities_for_tool(tool_name)

        if self.mode is AgentRunMode.FULL_ACCESS:
            return ToolAuthorization(
                AuthorizationOutcome.ALLOW_HOST,
                capabilities=capabilities,
            )

        if tool_name in _INERT_COMPATIBILITY_TOOL_NAMES:
            return ToolAuthorization(
                AuthorizationOutcome.ALLOW_SANDBOXED,
                capabilities=capabilities,
            )

        if not capabilities.known:
            return ToolAuthorization(
                AuthorizationOutcome.REQUIRE_APPROVAL,
                "Unknown tools require an exact user approval.",
                capabilities,
            )

        if self.mode is AgentRunMode.ASK and capabilities.effects & _ASK_RISK_EFFECTS:
            return ToolAuthorization(
                AuthorizationOutcome.REQUIRE_APPROVAL,
                "Ask mode requires an exact user approval for this action.",
                capabilities,
            )

        if _sandbox_approval_effects(capabilities):
            return ToolAuthorization(
                AuthorizationOutcome.REQUIRE_APPROVAL,
                "This action crosses the sandbox boundary and requires an exact user approval.",
                capabilities,
            )

        if (
            tool_capabilities.AGENT_ACTION_APPROVAL_GATE_ENABLED
            and security_context.external_untrusted_context_seen
            and capabilities.effects & POST_EXTERNAL_BLOCKED_EFFECTS
        ):
            return ToolAuthorization(
                AuthorizationOutcome.REQUIRE_APPROVAL,
                (
                    "External untrusted context influenced this run; "
                    "this exact action requires user approval."
                ),
                capabilities,
            )

        return ToolAuthorization(
            AuthorizationOutcome.ALLOW_SANDBOXED,
            capabilities=capabilities,
        )
