"""Tests for the MemMachine memory provider.

These tests do NOT require a running MemMachine server or the memmachine-client
package to be installed. We inject a fake ``memmachine_client`` module into
``sys.modules`` and test the provider's behaviour under both healthy and
degraded conditions.
"""

import asyncio
import json
import os
import sys
import uuid
from unittest.mock import MagicMock

import pytest


def run(coro):
    return asyncio.run(coro)


class FakeAddResult:
    def __init__(self, uid):
        self.uid = uid


class FakeMemory:
    """Mock MemMachine memory object returned by project.memory(...)."""

    def __init__(self, stored=None):
        self._stored = stored or []
        self.added = []
        self.searched = []
        self.deleted = []

    def add(self, text, metadata=None):
        uid = f"mm-{uuid.uuid4().hex[:8]}"
        self._stored.append({"uid": uid, "text": text, "metadata": metadata or {}})
        self.added.append((text, metadata))
        return [FakeAddResult(uid)]

    def search(self, query):
        self.searched.append(query)
        # Return a structure similar to the README example
        episodes = []
        for item in self._stored:
            episodes.append({
                "content": item["text"],
                "metadata": item["metadata"],
                "score": 0.95,
            })
        # Wrap in the nested structure the README shows
        return MagicMock(
            content=MagicMock(
                episodic_memory=MagicMock(
                    long_term_memory=MagicMock(episodes=episodes)
                )
            )
        )

    def delete(self, uid):
        self.deleted.append(uid)
        self._stored = [s for s in self._stored if s["uid"] != uid]


class FakeProject:
    def __init__(self, memory=None):
        self._memory = memory or FakeMemory()

    def memory(self, group_id, agent_id, user_id, session_id):
        return self._memory


class FakeClient:
    def __init__(self, project=None, **kwargs):
        self._project = project or FakeProject()

    def get_or_create_project(self, org_id, project_id):
        return self._project


# Inject a fake memmachine_client module so the provider can import it.
_fake_memmachine_client = MagicMock()
_fake_memmachine_client.MemMachineClient = FakeClient
sys.modules["memmachine_client"] = _fake_memmachine_client

from src.memmachine_provider import (
    MemMachineMemoryProvider,
    _extract_episodes,
    _episode_to_dict,
)


# ------------------------------------------------------------------
# Defensive result-mapping tests
# ------------------------------------------------------------------


def test_extract_episodes_reads_nested_structures():
    episodes = [{"content": "hello"}, {"content": "world"}]
    nested = MagicMock(
        content=MagicMock(
            episodic_memory=MagicMock(
                long_term_memory=MagicMock(episodes=episodes)
            )
        )
    )
    assert _extract_episodes(nested) == episodes


def test_extract_episodes_falls_back_to_plain_list():
    plain = [{"content": "a"}, {"content": "b"}]
    assert _extract_episodes(plain) == plain


def test_extract_episodes_falls_back_to_dict_key():
    assert _extract_episodes({"episodes": [1, 2]}) == [1, 2]
    assert _extract_episodes({"results": [3, 4]}) == [3, 4]
    assert _extract_episodes({"unknown": 5}) == []


def test_extract_episodes_returns_empty_for_none():
    assert _extract_episodes(None) == []


def test_episode_to_dict_handles_pydantic_and_dataclass():
    # Plain dict
    assert _episode_to_dict({"content": "x"}) == {"content": "x"}

    # Object with model_dump (Pydantic v2 style)
    obj = MagicMock()
    obj.model_dump.return_value = {"content": "y"}
    assert _episode_to_dict(obj) == {"content": "y"}

    # Object with dict() (Pydantic v1 style)
    obj2 = MagicMock()
    obj2.model_dump.side_effect = AttributeError
    obj2.dict.return_value = {"content": "z"}
    assert _episode_to_dict(obj2) == {"content": "z"}

    # Plain object with __dataclass_fields__ (class-level, like real dataclasses)
    class DC:
        __dataclass_fields__ = {}  # fake marker
        def __init__(self):
            self.content = "dc"
    dc = DC()
    assert _episode_to_dict(dc) == {"content": "dc"}

    # Unparseable falls back to str()
    assert _episode_to_dict(42) == {"content": "42"}


# ------------------------------------------------------------------
# Provider construction & health
# ------------------------------------------------------------------


def test_provider_healthy_when_client_succeeds(tmp_path):
    mapping_file = tmp_path / "mm_map.json"
    provider = MemMachineMemoryProvider(mapping_file=str(mapping_file))
    assert provider.healthy is True
    assert provider.provider_id == "memmachine"
    assert provider.display_name == "MemMachine"


def test_provider_degraded_when_import_missing(tmp_path):
    # Temporarily hide the fake module
    old_mod = sys.modules.pop("memmachine_client", None)
    try:
        mapping_file = tmp_path / "mm_map.json"
        provider = MemMachineMemoryProvider(mapping_file=str(mapping_file))
        assert provider.healthy is False
    finally:
        if old_mod is not None:
            sys.modules["memmachine_client"] = old_mod


# ------------------------------------------------------------------
# remember / recall / delete
# ------------------------------------------------------------------


def test_remember_returns_memory_record_with_uuid(tmp_path):
    mapping_file = tmp_path / "mm_map.json"
    fake_mem = FakeMemory()
    _fake_memmachine_client.MemMachineClient = lambda **kw: FakeClient(
        project=FakeProject(memory=fake_mem)
    )

    provider = MemMachineMemoryProvider(mapping_file=str(mapping_file))
    record = run(
        provider.remember(
            "User likes dark mode",
            owner="alice",
            session_id="s1",
            category="preference",
            source="user",
            metadata={"confidence": 0.9},
        )
    )

    assert record.text == "User likes dark mode"
    assert record.owner == "alice"
    assert record.session_id == "s1"
    assert record.category == "preference"
    assert record.source == "user"
    assert record.metadata["confidence"] == 0.9
    assert record.metadata["odysseus_id"] == record.id
    assert uuid.UUID(record.id)  # valid UUID format

    # Mapping file should contain the odysseus_id → mm uid relationship
    with open(mapping_file, "r", encoding="utf-8") as f:
        mapping = json.load(f)
    assert record.id in mapping


def test_recall_maps_search_results_to_hits(tmp_path):
    mapping_file = tmp_path / "mm_map.json"
    fake_mem = FakeMemory()
    _fake_memmachine_client.MemMachineClient = lambda **kw: FakeClient(
        project=FakeProject(memory=fake_mem)
    )

    provider = MemMachineMemoryProvider(mapping_file=str(mapping_file))
    run(provider.remember("Alice likes hiking", owner="alice"))
    hits = run(provider.recall("hobbies", owner="alice", top_k=5))

    assert len(hits) == 1
    assert hits[0].memory.text == "Alice likes hiking"
    assert hits[0].provider_id == "memmachine"
    assert hits[0].score == 0.95


def test_recall_returns_empty_when_not_healthy(tmp_path):
    mapping_file = tmp_path / "mm_map.json"
    provider = MemMachineMemoryProvider(mapping_file=str(mapping_file))
    provider._healthy = False
    hits = run(provider.recall("anything"))
    assert hits == []


def test_delete_uses_mapping_file(tmp_path):
    mapping_file = tmp_path / "mm_map.json"
    fake_mem = FakeMemory()
    _fake_memmachine_client.MemMachineClient = lambda **kw: FakeClient(
        project=FakeProject(memory=fake_mem)
    )

    provider = MemMachineMemoryProvider(mapping_file=str(mapping_file))
    record = run(provider.remember("Delete me", owner="alice"))
    assert len(fake_mem.added) == 1

    ok = run(provider.delete(record.id, owner="alice"))
    assert ok is True
    assert len(fake_mem.deleted) == 1

    # Mapping should be cleared after delete
    with open(mapping_file, "r", encoding="utf-8") as f:
        mapping = json.load(f)
    assert record.id not in mapping


def test_delete_returns_false_for_unknown_id(tmp_path):
    mapping_file = tmp_path / "mm_map.json"
    fake_mem = FakeMemory()
    _fake_memmachine_client.MemMachineClient = lambda **kw: FakeClient(
        project=FakeProject(memory=fake_mem)
    )

    provider = MemMachineMemoryProvider(mapping_file=str(mapping_file))
    ok = run(provider.delete("non-existent-id", owner="alice"))
    assert ok is False


# ------------------------------------------------------------------
# list_memories
# ------------------------------------------------------------------


def test_list_memories_delegates_to_recall(tmp_path):
    mapping_file = tmp_path / "mm_map.json"
    fake_mem = FakeMemory()
    _fake_memmachine_client.MemMachineClient = lambda **kw: FakeClient(
        project=FakeProject(memory=fake_mem)
    )

    provider = MemMachineMemoryProvider(mapping_file=str(mapping_file))
    run(provider.remember("Item 1", owner="alice"))
    run(provider.remember("Item 2", owner="alice"))
    records = run(provider.list_memories(owner="alice", limit=10))

    assert len(records) == 2
    assert records[0].text == "Item 1"
    assert records[1].text == "Item 2"


# ------------------------------------------------------------------
# Env-var overrides
# ------------------------------------------------------------------


def test_env_vars_override_defaults(monkeypatch):
    monkeypatch.setenv("MEMMACHINE_URL", "http://mm.example:9000")
    monkeypatch.setenv("MEMMACHINE_ORG_ID", "my-org")
    monkeypatch.setenv("MEMMACHINE_PROJECT_ID", "my-project")
    monkeypatch.setenv("MEMMACHINE_GROUP_ID", "my-group")
    monkeypatch.setenv("MEMMACHINE_AGENT_ID", "my-agent")

    provider = MemMachineMemoryProvider(mapping_file="/dev/null")
    assert provider._base_url == "http://mm.example:9000"
    assert provider._org_id == "my-org"
    assert provider._project_id == "my-project"
    assert provider._group_id == "my-group"
    assert provider._agent_id == "my-agent"
