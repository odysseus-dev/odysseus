from __future__ import annotations

from dataclasses import dataclass

ALLOWED_AGENT_PROFILES = ("odysseus", "opencode")
DEFAULT_AGENT_PROFILE = "odysseus"


@dataclass(frozen=True)
class SessionBinding:
    conversation_id: str | None
    agent_profile_id: str


def normalize_agent_profile_id(value: str | None) -> str:
    if value in ALLOWED_AGENT_PROFILES:
        return value
    return DEFAULT_AGENT_PROFILE


def conversation_kwarg(session_id: str, binding: SessionBinding) -> str | None:
    if binding.conversation_id and binding.conversation_id != session_id:
        return binding.conversation_id
    return None


class MemoryBindingStore:
    def __init__(self) -> None:
        self._rows: dict[str, SessionBinding] = {}

    def get(self, session_id: str) -> SessionBinding:
        return self._rows.get(
            session_id,
            SessionBinding(None, DEFAULT_AGENT_PROFILE),
        )

    def put(self, session_id: str, binding: SessionBinding) -> None:
        self._rows[session_id] = SessionBinding(
            binding.conversation_id,
            normalize_agent_profile_id(binding.agent_profile_id),
        )
