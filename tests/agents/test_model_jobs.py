from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.model_jobs import (  # noqa: E402
    InvalidArchetype,
    ModelJobExecutor,
    ModelJobResult,
)


@pytest.fixture
def model_job_archetype():
    return SimpleNamespace(
        id="session-title",
        version=1,
        execution_kind="model-job",
        input_schema="SessionTitleInputV1",
        result_schema="SessionTitleResultV1",
        token_limit=64,
        timeout_seconds=5,
        tools=[],
        conversation_policy=None,
        workspace_policy=None,
        delegation_policy=None,
    )


@pytest.fixture
def executor():
    def invoke(prompt, **kwargs):
        return {"title": "Hello"}

    return ModelJobExecutor(invoke=invoke)


def test_model_job_rejects_agent_capabilities(executor, model_job_archetype):
    model_job_archetype.tools = ["odysseus.mail.send"]
    with pytest.raises(InvalidArchetype):
        executor.execute(model_job_archetype, {}, owner="u1")


def test_model_job_rejects_conversation_workspace_delegation(executor, model_job_archetype):
    model_job_archetype.conversation_policy = "durable"
    with pytest.raises(InvalidArchetype):
        executor.execute(model_job_archetype, {}, owner="u1")
    model_job_archetype.conversation_policy = None
    model_job_archetype.workspace_policy = "research"
    with pytest.raises(InvalidArchetype):
        executor.execute(model_job_archetype, {}, owner="u1")
    model_job_archetype.workspace_policy = None
    model_job_archetype.delegation_policy = {"max_depth": 1}
    with pytest.raises(InvalidArchetype):
        executor.execute(model_job_archetype, {}, owner="u1")


def test_model_job_returns_typed_output(executor, model_job_archetype):
    result = executor.execute(model_job_archetype, {"text": "hi"}, owner="u1")
    assert isinstance(result, ModelJobResult)
    assert result.output == {"title": "Hello"}
    assert result.owner == "u1"
    assert result.conversation_id is None
    assert result.audit["archetype"] == "session-title"


def test_schema_failure_is_raised(model_job_archetype):
    def invoke(prompt, **kwargs):
        return "not-an-object"

    executor = ModelJobExecutor(invoke=invoke)
    with pytest.raises(InvalidArchetype, match="schema"):
        executor.execute(model_job_archetype, {"text": "hi"}, owner="u1")


def test_bounded_retry_then_succeeds(model_job_archetype):
    calls = {"n": 0}

    def invoke(prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("transient")
        return {"title": "ok"}

    executor = ModelJobExecutor(invoke=invoke, max_retries=2)
    result = executor.execute(model_job_archetype, {"text": "hi"}, owner="u1")
    assert result.output["title"] == "ok"
    assert calls["n"] == 2
