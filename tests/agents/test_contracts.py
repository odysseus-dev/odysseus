from __future__ import annotations

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.contracts import (  # noqa: E402
    AutomationExecutionRef,
    ExecutionKind,
    ModelJobRef,
    OdysseusProfilePolicy,
)


def test_agent_and_model_job_ids_cannot_be_confused():
    assert AutomationExecutionRef("r1", "c1") != ModelJobRef("m1")


def test_execution_kind_values_are_frozen():
    assert ExecutionKind.AGENT == "agent"
    assert ExecutionKind.MODEL_JOB == "model-job"
    with pytest.raises(AttributeError):
        ExecutionKind.AGENT = "chat"  # type: ignore[misc]


def test_execution_refs_are_immutable():
    ref = AutomationExecutionRef("r1", "c1")
    with pytest.raises(AttributeError):
        ref.conversation_id = "c2"  # type: ignore[misc]
    job = ModelJobRef("m1")
    with pytest.raises(AttributeError):
        job.model_job_id = "m2"  # type: ignore[misc]


def test_profile_requires_exact_upstream_revision():
    with pytest.raises(ValueError):
        OdysseusProfilePolicy(agent_profile_id="hermes", agent_profile_revision=None)
