from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.agents._import_agents import ensure_agents_package

ensure_agents_package()

from services.agents.archetypes import (  # noqa: E402
    TaskArchetype,
    load_archetypes,
)
from services.agents.contracts import ExecutionKind  # noqa: E402

_ARCHETYPE_DIR = (
    Path(__file__).resolve().parents[2] / "config" / "agents" / "archetypes"
)


def test_shipped_archetypes_load_and_require_result_schema():
    archetypes = load_archetypes(_ARCHETYPE_DIR)
    by_id = {(item.id, item.version): item for item in archetypes}
    assert ("chat", 1) in by_id
    assert ("deep-research", 1) in by_id
    assert ("session-title", 1) in by_id
    for item in archetypes:
        assert item.result_schema
        assert item.input_schema
        assert isinstance(item, TaskArchetype)


def test_load_rejects_unknown_keys(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "id": "chat",
                "version": 1,
                "execution_kind": "agent",
                "input_schema": "ChatInputV1",
                "result_schema": "ChatResultV1",
                "mystery": True,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown"):
        load_archetypes(tmp_path)


def test_load_rejects_duplicate_id_version(tmp_path):
    payload = {
        "id": "chat",
        "version": 1,
        "execution_kind": "agent",
        "input_schema": "ChatInputV1",
        "result_schema": "ChatResultV1",
    }
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_archetypes(tmp_path)


def test_load_rejects_missing_result_schema(tmp_path):
    (tmp_path / "chat.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "chat",
                "version": 1,
                "execution_kind": "agent",
                "input_schema": "ChatInputV1",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="result_schema"):
        load_archetypes(tmp_path)


def test_agent_only_fields_rejected_on_model_job(tmp_path):
    (tmp_path / "job.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "session-title",
                "version": 1,
                "execution_kind": "model-job",
                "input_schema": "SessionTitleInputV1",
                "result_schema": "SessionTitleResultV1",
                "conversation_policy": "durable",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="conversation_policy"):
        load_archetypes(tmp_path)


def test_model_job_only_fields_rejected_on_agent(tmp_path):
    (tmp_path / "chat.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "chat",
                "version": 1,
                "execution_kind": "agent",
                "input_schema": "ChatInputV1",
                "result_schema": "ChatResultV1",
                "token_limit": 512,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="token_limit"):
        load_archetypes(tmp_path)


def test_chat_archetype_is_agent_kind():
    archetypes = load_archetypes(_ARCHETYPE_DIR)
    chat = next(item for item in archetypes if item.id == "chat")
    assert chat.execution_kind is ExecutionKind.AGENT
    assert chat.conversation_policy
    job = next(item for item in archetypes if item.execution_kind is ExecutionKind.MODEL_JOB)
    assert job.token_limit is not None
    assert job.conversation_policy is None
