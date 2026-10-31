"""Reproductions for issue #6898, using the real consolidation action.

Only the data directory and endpoint discovery are replaced. No model is
called and no real user's memory store is read or changed.
"""

import json

import pytest

from tests.test_builtin_memory_consolidation import _import_consolidate_action


@pytest.fixture
def consolidate_without_model(monkeypatch, tmp_path):
    from src import constants, task_endpoint

    action = _import_consolidate_action()
    from src.builtin_actions import TaskNoop

    data_dir = tmp_path / "memory-data"
    data_dir.mkdir()
    memory_file = data_dir / "memory.json"
    monkeypatch.setattr(constants, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(task_endpoint, "resolve_task_candidates", lambda *args, **kwargs: [])

    async def run(memories):
        for memory in memories:
            memory.update(timestamp=1, source="user", uses=0)
        memory_file.write_text(json.dumps(memories, ensure_ascii=False), encoding="utf-8")
        try:
            message, ok = await action("test-owner")
            assert ok, message
        except TaskNoop:
            pass
        return json.loads(memory_file.read_text(encoding="utf-8"))

    return run


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param("Dog is named Bjorn.", "Dog is named Bjarne.", id="different-names"),
        pytest.param("Møte med Åse torsdag", "Møte med Åge torsdag", id="norwegian-names"),
        pytest.param("User runs 16 local servers.", "User runs 64 local servers.", id="different-numbers"),
        pytest.param("User wants notifications enabled.", "User wants notifications disabled.", id="opposite-setting"),
        pytest.param("User is allergic to peanuts.", "User is not allergic to peanuts.", id="negated-fact"),
        pytest.param("User works with Müller.", "User works with Möller.", id="diacritic-collision"),
        pytest.param("User lives in Oslo.", "User lives in Otta.", id="different-places"),
        pytest.param("Кота зовут Барсик.", "Кота зовут Мурзик.", id="cyrillic"),
        pytest.param("猫の名前はミケです。", "猫の名前はタマです。", id="japanese"),
        pytest.param("The storage bucket is backup", "The storage bucket is backups", id="substring-value"),
        pytest.param("User is now here.", "User is nowhere.", id="word-boundaries"),
        pytest.param("User's account is user.name", "User's account is username", id="significant-punctuation"),
    ],
)
async def test_consolidation_preserves_distinct_facts(consolidate_without_model, left, right):
    memories = [
        {"id": "left", "owner": "test-owner", "text": left, "category": "fact"},
        {"id": "right", "owner": "test-owner", "text": right, "category": "fact"},
    ]

    assert await consolidate_without_model(memories) == memories


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("left", "right"),
    [
        pytest.param("User likes local models.", "  USER likes   local models.  ", id="case-whitespace"),
        pytest.param("Møte med Åse torsdag.", "Møte med A\u030ase torsdag.", id="canonical-unicode"),
        pytest.param("Кота зовут Барсик.", "Кота зовут Барсик.", id="cyrillic-exact"),
        pytest.param("猫の名前はミケです。", "猫の名前はミケです。", id="japanese-exact"),
        pytest.param("User lives on Straße.", "User lives on STRASSE.", id="unicode-casefold"),
    ],
)
async def test_consolidation_deduplicates_equivalent_text_and_keeps_pinned(
    consolidate_without_model, left, right
):
    memories = [
        {"id": "left", "owner": "test-owner", "text": left, "category": "fact"},
        {"id": "right", "owner": "test-owner", "text": right, "category": "fact", "pinned": True},
    ]

    assert await consolidate_without_model(memories) == [memories[1]]


@pytest.mark.asyncio
async def test_consolidation_preserves_same_text_in_different_categories(consolidate_without_model):
    memories = [
        {"id": "fact", "owner": "test-owner", "text": "Keep this record.", "category": "fact"},
        {"id": "instruction", "owner": "test-owner", "text": "Keep this record.", "category": "instruction"},
    ]

    assert await consolidate_without_model(memories) == memories
