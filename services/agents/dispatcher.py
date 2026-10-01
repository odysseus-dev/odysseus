"""Governed OpenHands dispatch. Interactive turns use Agent Server (continued_run)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import AutomationExecutionRef
from .openhands_client import OpenHandsClient
from .session_binding import normalize_agent_profile_id

_ROOT = Path(__file__).resolve().parents[2]
_ARCHETYPES = _ROOT / "config" / "agents" / "archetypes"
_POLICY = _ROOT / "config" / "agents" / "profile-policy.yaml"


@dataclass(frozen=True)
class DispatchRequest:
    request_id: str
    archetype: str
    payload: dict[str, Any]
    conversation_id: str | None = None
    workspace_grants: tuple[str, ...] = ()
    agent_profile_id: str = "odysseus"


class AgentDispatcher:
    def __init__(self, client: OpenHandsClient | Any | None = None) -> None:
        self.client = client or OpenHandsClient.from_env()
        self._idempotency: dict[str, AutomationExecutionRef] = {}

    def _resolve(self, archetype_id: str) -> tuple[int, int]:
        try:
            from .archetypes import load_archetypes
            from .profiles import load_profile_policies, select_compatible_profiles

            archetypes = load_archetypes(_ARCHETYPES)
            match = next((item for item in archetypes if item.id == archetype_id), None)
            version = match.version if match else 1
            if match is None:
                return 1, version
            selected = select_compatible_profiles(match, load_profile_policies(_POLICY))
            revision = selected[0].agent_profile_revision if selected else 1
            return int(revision or 1), int(version)
        except Exception:
            return 1, 1

    def dispatch(
        self,
        request: DispatchRequest | None = None,
        **kwargs: Any,
    ) -> AutomationExecutionRef:
        if request is None:
            request = DispatchRequest(
                request_id=str(kwargs["request_id"]),
                archetype=str(kwargs["archetype"]),
                payload=dict(kwargs.get("payload") or {}),
                conversation_id=kwargs.get("conversation_id"),
                workspace_grants=tuple(kwargs.get("workspace_grants") or ()),
                agent_profile_id=normalize_agent_profile_id(kwargs.get("agent_profile_id")),
            )
        if request.request_id in self._idempotency:
            return self._idempotency[request.request_id]
        profile_revision, archetype_version = self._resolve(request.archetype)
        message = str(request.payload.get("text") or request.payload.get("query") or "")
        resumed = self.client.create_or_resume(
            conversation_id=request.conversation_id,
            message=message,
            request_id=request.request_id,
            profile_revision=profile_revision,
            archetype_version=archetype_version,
            workspace_grants=request.workspace_grants,
            credential_delivery_mode="broker",
            agent_profile_id=normalize_agent_profile_id(request.agent_profile_id),
        )
        ref = AutomationExecutionRef(
            automation_execution_id=resumed.execution_id,
            conversation_id=resumed.conversation_id,
            resolved_model=getattr(resumed, "resolved_model", None),
        )
        self._idempotency[request.request_id] = ref
        return ref
