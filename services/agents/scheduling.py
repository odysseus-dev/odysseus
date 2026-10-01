"""Schedule fires create a fresh Automation/Agent Server execution and token."""

from __future__ import annotations

from dataclasses import dataclass

from .delegation import DelegationAuthority
from .dispatcher import AgentDispatcher, DispatchRequest


@dataclass(frozen=True)
class ScheduleFire:
    execution_id: str
    token_id: str
    policy_revision: int
    schedule_id: str


class AutomationScheduler:
    def __init__(
        self,
        dispatcher: AgentDispatcher | None = None,
        authority: DelegationAuthority | None = None,
    ) -> None:
        self.dispatcher = dispatcher or AgentDispatcher()
        self.authority = authority or DelegationAuthority(hmac_key=b"schedule-hmac-key-32-bytes!!!!!")
        self.policy_revision = 1
        self._seq = 0

    def set_policy_revision(self, revision: int) -> None:
        self.policy_revision = int(revision)

    def fire(self, schedule_id: str) -> ScheduleFire:
        self._seq += 1
        request_id = f"{schedule_id}:{self._seq}:{self.policy_revision}"
        ref = self.dispatcher.dispatch(
            DispatchRequest(
                request_id=request_id,
                archetype="chat",
                payload={"text": f"schedule:{schedule_id}"},
            )
        )
        token = self.authority.issue(
            audience="odysseus-mcp",
            execution_id=ref.automation_execution_id,
            conversation_id=ref.conversation_id,
            owner="schedule",
            scopes={"notes.read"},
            resources=set(),
            profile_id="odysseus",
            profile_revision=self.policy_revision,
            archetype_id="chat",
            archetype_version=1,
            budget=50,
            max_depth=0,
            ttl_seconds=60,
        )
        return ScheduleFire(
            execution_id=ref.automation_execution_id,
            token_id=token.claims.token_id,
            policy_revision=self.policy_revision,
            schedule_id=schedule_id,
        )
