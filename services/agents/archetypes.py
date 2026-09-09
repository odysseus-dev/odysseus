"""Strict loaders for versioned task archetypes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .contracts import ExecutionKind

_SHARED_REQUIRED = ("id", "version", "execution_kind", "input_schema", "result_schema")
_SHARED_OPTIONAL = {
    "required_artifacts": (),
    "optional_artifacts": (),
    "required_capabilities": (),
    "optional_capabilities": (),
    "risk_class": "low",
    "approval_policy": "confirm-risky",
    "resource_constraints": None,
    "budget": "default",
    "timeout_seconds": 60,
    "audit_requirements": (),
    "completion_criteria": "terminal",
    "failure_retry_policy": "transient-only",
    "compatible_runtime_traits": (),
}
_AGENT_KEYS = (
    "preferred_profile_traits",
    "conversation_policy",
    "workspace_policy",
    "delegation_policy",
    "checkpoint_resume_policy",
    "interaction_policy",
)
_MODEL_JOB_KEYS = (
    "token_limit",
    "temperature",
    "tools",
    "model_capability_requirements",
    "determinism_policy",
)
_ALLOWED = set(_SHARED_REQUIRED) | set(_SHARED_OPTIONAL) | set(_AGENT_KEYS) | set(_MODEL_JOB_KEYS)


@dataclass(frozen=True)
class TaskArchetype:
    id: str
    version: int
    execution_kind: ExecutionKind
    input_schema: str
    result_schema: str
    required_artifacts: tuple[str, ...] = ()
    optional_artifacts: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    optional_capabilities: tuple[str, ...] = ()
    risk_class: str = "low"
    approval_policy: str = "confirm-risky"
    resource_constraints: Mapping[str, Any] | None = None
    budget: str = "default"
    timeout_seconds: int = 60
    audit_requirements: tuple[str, ...] = ()
    completion_criteria: str = "terminal"
    failure_retry_policy: str = "transient-only"
    compatible_runtime_traits: tuple[str, ...] = ()
    preferred_profile_traits: tuple[str, ...] | None = None
    conversation_policy: str | None = None
    workspace_policy: str | None = None
    delegation_policy: Mapping[str, Any] | None = None
    checkpoint_resume_policy: str | None = None
    interaction_policy: str | None = None
    token_limit: int | None = None
    temperature: float | None = None
    tools: tuple[str, ...] | None = None
    model_capability_requirements: tuple[str, ...] | None = None
    determinism_policy: str | None = None


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def load_archetypes(directory: str | Path) -> list[TaskArchetype]:
    root = Path(directory)
    seen: set[tuple[str, int]] = set()
    loaded: list[TaskArchetype] = []
    for path in sorted(root.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict):
            raise ValueError(f"{path}: archetype must be a mapping")
        loaded.append(_parse_archetype(payload, seen, source=path.name))
    return loaded


def _parse_archetype(
    payload: dict[str, Any],
    seen: set[tuple[str, int]],
    *,
    source: str,
) -> TaskArchetype:
    unknown = set(payload) - _ALLOWED
    if unknown:
        raise ValueError(f"{source}: unknown keys {sorted(unknown)}")
    missing = [key for key in _SHARED_REQUIRED if key not in payload]
    if missing:
        raise ValueError(f"{source}: missing {missing[0]}")
    kind = ExecutionKind(payload["execution_kind"])
    if kind is ExecutionKind.AGENT:
        illegal = [key for key in _MODEL_JOB_KEYS if key in payload]
        if illegal:
            raise ValueError(f"{source}: {illegal[0]} is model-job-only")
    else:
        illegal = [key for key in _AGENT_KEYS if key in payload]
        if illegal:
            raise ValueError(f"{source}: {illegal[0]} is agent-only")
    identity = (str(payload["id"]), int(payload["version"]))
    if identity in seen:
        raise ValueError(f"{source}: duplicate (id, version) {identity}")
    seen.add(identity)
    values = {key: payload.get(key, default) for key, default in _SHARED_OPTIONAL.items()}
    return TaskArchetype(
        id=identity[0],
        version=identity[1],
        execution_kind=kind,
        input_schema=str(payload["input_schema"]),
        result_schema=str(payload["result_schema"]),
        required_artifacts=_as_tuple(values["required_artifacts"]),
        optional_artifacts=_as_tuple(values["optional_artifacts"]),
        required_capabilities=_as_tuple(values["required_capabilities"]),
        optional_capabilities=_as_tuple(values["optional_capabilities"]),
        risk_class=str(values["risk_class"]),
        approval_policy=str(values["approval_policy"]),
        resource_constraints=values["resource_constraints"],
        budget=str(values["budget"]),
        timeout_seconds=int(values["timeout_seconds"]),
        audit_requirements=_as_tuple(values["audit_requirements"]),
        completion_criteria=str(values["completion_criteria"]),
        failure_retry_policy=str(values["failure_retry_policy"]),
        compatible_runtime_traits=_as_tuple(values["compatible_runtime_traits"]),
        preferred_profile_traits=_as_tuple(payload["preferred_profile_traits"])
        if "preferred_profile_traits" in payload
        else None,
        conversation_policy=payload.get("conversation_policy"),
        workspace_policy=payload.get("workspace_policy"),
        delegation_policy=payload.get("delegation_policy"),
        checkpoint_resume_policy=payload.get("checkpoint_resume_policy"),
        interaction_policy=payload.get("interaction_policy"),
        token_limit=payload.get("token_limit"),
        temperature=payload.get("temperature"),
        tools=_as_tuple(payload["tools"]) if "tools" in payload else None,
        model_capability_requirements=_as_tuple(payload["model_capability_requirements"])
        if "model_capability_requirements" in payload
        else None,
        determinism_policy=payload.get("determinism_policy"),
    )
