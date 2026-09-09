"""Frozen execution identifiers shared by agent runs and model jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ExecutionKind(StrEnum):
    AGENT = "agent"
    MODEL_JOB = "model-job"


@dataclass(frozen=True)
class AutomationExecutionRef:
    automation_execution_id: str
    conversation_id: str


@dataclass(frozen=True)
class ModelJobRef:
    model_job_id: str


@dataclass(frozen=True)
class OdysseusProfilePolicy:
    agent_profile_id: str
    agent_profile_revision: int | None
    capabilities: tuple[str, ...] = ()
    archetypes: tuple[str, ...] = ()
    risk_class: str = "low"
    delegation_depth: int = 0
    workspace_classes: tuple[str, ...] = ()
    budget_class: str = "default"
    distribution_metadata: dict[str, Any] = field(default_factory=dict)
    policy_revision: int | None = None

    def __post_init__(self) -> None:
        if self.agent_profile_revision is None:
            raise ValueError("agent_profile_revision is required")
