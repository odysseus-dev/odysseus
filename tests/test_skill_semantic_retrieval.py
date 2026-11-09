"""Native semantic skill ranking with lexical and capability-safe fallback."""

import pytest

from services.memory.skills import SkillsManager
from src import skill_index, tool_index


class _IntentLane:
    name = "fastembed"
    fingerprint = "intent-lane-v1"

    def __init__(self):
        self.document_encode_calls = 0

    def encode(self, texts):
        vectors = []
        if len(texts) > 1:
            self.document_encode_calls += 1
        for text in texts:
            lowered = text.lower()
            if "orchestrate attendees" in lowered or "coordinate calendars" in lowered:
                vectors.append([1.0, 0.0])
            else:
                vectors.append([0.0, 1.0])
        return vectors


class _ReadyIndex:
    def __init__(self, lane):
        self.embedding_lanes = (lane,)


def _skill(name, description, *, requires=None, fallback_for=None):
    return {
        "name": name,
        "description": description,
        "when_to_use": "",
        "tags": [],
        "procedure": [],
        "requires_toolsets": list(requires or []),
        "fallback_for_toolsets": list(fallback_for or []),
        "status": "published",
        "confidence": 0.8,
    }


@pytest.fixture
def semantic_lane(monkeypatch):
    lane = _IntentLane()
    index = _ReadyIndex(lane)
    skill_index.reset_skill_index_cache()
    monkeypatch.setattr(tool_index, "get_ready_tool_index", lambda: index)
    yield lane
    skill_index.reset_skill_index_cache()


def test_semantic_ranking_recovers_paraphrase_with_no_lexical_overlap(tmp_path, semantic_lane):
    manager = SkillsManager(str(tmp_path))
    skills = [
        _skill("meeting-coordination", "coordinate calendars and confirm a viable time"),
        _skill("video-inspection", "extract representative frames from a recording"),
    ]

    results = manager.get_relevant_skills(
        "orchestrate attendees without double booking anyone",
        skills=skills,
        threshold=0.25,
    )

    assert [skill["name"] for skill in results] == ["meeting-coordination"]


def test_semantic_corpus_vectors_are_cached(tmp_path, semantic_lane):
    manager = SkillsManager(str(tmp_path))
    skills = [
        _skill("meeting-coordination", "coordinate calendars and confirm a viable time"),
        _skill("video-inspection", "extract representative frames from a recording"),
    ]

    manager.get_relevant_skills("orchestrate attendees for a launch", skills=skills)
    manager.get_relevant_skills("orchestrate attendees for an offsite", skills=skills)

    assert semantic_lane.document_encode_calls == 1


def test_semantic_match_cannot_bypass_required_tool_capability(tmp_path, semantic_lane):
    manager = SkillsManager(str(tmp_path))
    skills = [
        _skill(
            "meeting-coordination",
            "coordinate calendars and confirm a viable time",
            requires=["manage_calendar"],
        ),
    ]

    unavailable = manager.get_relevant_skills(
        "orchestrate attendees without double booking anyone",
        skills=skills,
        available_toolsets={"web_search"},
    )
    available = manager.get_relevant_skills(
        "orchestrate attendees without double booking anyone",
        skills=skills,
        available_toolsets={"manage_calendar"},
    )

    assert unavailable == []
    assert [skill["name"] for skill in available] == ["meeting-coordination"]


def test_fallback_skill_is_hidden_when_native_tool_is_available(tmp_path, semantic_lane):
    manager = SkillsManager(str(tmp_path))
    skills = [
        _skill(
            "manual-meeting-fallback",
            "coordinate calendars and confirm a viable time",
            fallback_for=["manage_calendar"],
        ),
    ]

    assert manager.get_relevant_skills(
        "orchestrate attendees without double booking anyone",
        skills=skills,
        available_toolsets={"manage_calendar"},
    ) == []


def test_semantic_ranking_can_be_disabled_without_breaking_lexical_fallback(
    tmp_path, semantic_lane, monkeypatch
):
    manager = SkillsManager(str(tmp_path))
    skills = [
        _skill("meeting-coordination", "coordinate calendars and confirm a viable time"),
    ]
    monkeypatch.setenv("ODYSSEUS_SKILL_SEMANTIC_RETRIEVAL", "0")

    semantic_only = manager.get_relevant_skills(
        "orchestrate attendees without double booking anyone",
        skills=skills,
    )
    lexical = manager.get_relevant_skills(
        "coordinate calendars and confirm a viable time",
        skills=skills,
    )

    assert semantic_only == []
    assert [skill["name"] for skill in lexical] == ["meeting-coordination"]


def test_no_ready_embedding_index_keeps_existing_lexical_behavior(tmp_path, monkeypatch):
    manager = SkillsManager(str(tmp_path))
    skills = [_skill("git-helper", "version control procedure")]
    monkeypatch.setattr(tool_index, "get_ready_tool_index", lambda: None)

    results = manager.get_relevant_skills("version control procedure", skills=skills)

    assert [skill["name"] for skill in results] == ["git-helper"]
