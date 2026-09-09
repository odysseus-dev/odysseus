"""Bounded model-job executor. No conversation, workspace, or delegation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


class InvalidArchetype(Exception):
    """Archetype is not a bounded model job or produced an invalid result."""


@dataclass(frozen=True)
class ModelJobResult:
    output: dict[str, Any]
    owner: str
    conversation_id: None
    audit: dict[str, Any]


class ModelJobExecutor:
    def __init__(self, invoke: Callable[..., Any] | None = None, max_retries: int = 1) -> None:
        if invoke is None:
            from src.ai_interaction import invoke_structured_model

            invoke = invoke_structured_model
        self.invoke = invoke
        self.max_retries = max_retries

    def execute(self, archetype: Any, payload: dict[str, Any], owner: str) -> ModelJobResult:
        kind = getattr(archetype, "execution_kind", None)
        kind_value = getattr(kind, "value", kind)
        if kind_value not in {"model-job", None} and str(kind_value) != "model-job":
            if str(kind_value) == "agent":
                raise InvalidArchetype("agent archetype cannot run as a model job")
        tools = list(getattr(archetype, "tools", None) or [])
        if tools:
            raise InvalidArchetype("model jobs cannot use arbitrary tools")
        if getattr(archetype, "conversation_policy", None):
            raise InvalidArchetype("model jobs cannot open conversations")
        if getattr(archetype, "workspace_policy", None):
            raise InvalidArchetype("model jobs cannot mount workspaces")
        if getattr(archetype, "delegation_policy", None):
            raise InvalidArchetype("model jobs cannot delegate")
        last_error: Exception | None = None
        output: Any = None
        attempts = max(self.max_retries, 1)
        for _ in range(attempts):
            try:
                output = self.invoke(payload, archetype=archetype, owner=owner)
                last_error = None
                break
            except Exception as exc:  # transient only; next attempt or raise
                last_error = exc
        if last_error is not None:
            raise last_error
        if not isinstance(output, dict):
            raise InvalidArchetype("schema failure: result must be an object")
        return ModelJobResult(
            output=output,
            owner=owner,
            conversation_id=None,
            audit={
                "archetype": getattr(archetype, "id", "unknown"),
                "version": getattr(archetype, "version", None),
                "token_limit": getattr(archetype, "token_limit", None),
                "timeout_seconds": getattr(archetype, "timeout_seconds", None),
            },
        )
