"""Regression tests for _extract_json_object using json.JSONDecoder().raw_decode().

 alteixeira20 requested these cases in review of #3978:
 - first complete object extracted when two JSON objects are present;
 - trailing stray brace after a valid first object;
 - braces inside JSON string values;
 - maybe_extract_skill() end-to-end with multi-object output.
"""
import json

import pytest

from services.memory import skill_extractor


# -- _extract_json_object unit tests ----------------------------------------


def test_first_of_two_objects():
    """When the LLM emits two JSON objects back-to-back, return the first."""
    resp = (
        '{"title": "Correct", "steps": ["a"]}'
        '{"title": "Wrong", "steps": ["b"]}'
    )
    data = skill_extractor._extract_json_object(resp)
    assert isinstance(data, dict)
    assert data["title"] == "Correct"


def test_trailing_stray_brace_after_valid_object():
    """A lone '}' after a complete object must not break extraction."""
    resp = '{"title": "Good", "steps": ["x"]} trailing brace }'
    data = skill_extractor._extract_json_object(resp)
    assert isinstance(data, dict)
    assert data["title"] == "Good"


def test_braces_inside_string_values():
    """Braces that appear inside quoted JSON strings must not confuse parsing."""
    cases = [
        '{"title":"Use { brace safely","steps":[]}',
        '{"title":"Use } brace safely","steps":[]}',
        '{"title":"Use \\"quoted {\\" text","steps":[]}',
    ]
    for case in cases:
        data = skill_extractor._extract_json_object(case)
        assert isinstance(data, dict), f"Failed for: {case}"
        assert "title" in data


def test_braces_inside_prose_before_object():
    """Prose with {placeholder} braces before a real JSON object."""
    resp = (
        'The model uses {variable} syntax and also {another} placeholder. '
        '{"title": "Real skill", "problem": "p", "solution": "s", '
        '"steps": ["one"], "tags": ["t"], "confidence": 0.8}'
    )
    data = skill_extractor._extract_json_object(resp)
    assert isinstance(data, dict)
    assert data["title"] == "Real skill"


def test_non_dict_json_returns_none():
    """A JSON array or primitive at a '{' start should not be returned."""
    # This shouldn't happen often but raw_decode could return a non-dict
    assert skill_extractor._extract_json_object("[1, 2, 3]") is None


def test_no_brace_returns_none():
    assert skill_extractor._extract_json_object("no json here") is None
    assert skill_extractor._extract_json_object("") is None
    assert skill_extractor._extract_json_object(None) is None


# -- maybe_extract_skill end-to-end -----------------------------------------


class _FakeSession:
    session_id = "s1"

    def get_context_messages(self):
        return [
            {"role": "user", "content": "Walk me through the task"},
            {"role": "assistant", "content": "Sure, here is the runbook..."},
        ]


class _FakeSkillsManager:
    def __init__(self):
        self.added = []

    def load(self, owner=None):
        return []

    def add_skill(self, **kwargs):
        self.added.append(kwargs)
        return {"id": "skill-1", **kwargs}


_MULTI_OBJECT_RESPONSE = (
    '{"title": "First correct skill", "problem": "p", "solution": "s", '
    '"steps": ["a", "b"], "tags": ["t1"], "confidence": 0.9}'
    '{"title": "Second wrong skill", "problem": "p2", "solution": "s2", '
    '"steps": ["c"], "tags": ["t2"], "confidence": 0.7}'
)


async def test_maybe_extract_skill_persists_first_of_multi_object(monkeypatch):
    """End-to-end: when LLM returns two skill objects, persist only the first."""

    async def fake_llm_call_async(*args, **kwargs):
        return _MULTI_OBJECT_RESPONSE

    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    skills_manager = _FakeSkillsManager()
    entry = await skill_extractor.maybe_extract_skill(
        _FakeSession(),
        skills_manager,
        endpoint_url="http://endpoint",
        model="test-model",
        headers={},
        round_count=3,
        tool_count=3,
        owner="alice",
    )

    assert entry is not None
    assert entry["title"] == "First correct skill"
    assert len(skills_manager.added) == 1
    assert skills_manager.added[0]["title"] == "First correct skill"


_BRACES_IN_STRINGS_RESPONSE = (
    '{"title": "Use {braces} in config", "problem": "templates need {var} syntax", '
    '"solution": "escape them", "steps": ["step1"], "tags": ["config"], '
    '"confidence": 0.8}'
)


async def test_maybe_extract_skill_with_braces_in_strings(monkeypatch):
    """End-to-end: braces inside string values do not break extraction."""

    async def fake_llm_call_async(*args, **kwargs):
        return _BRACES_IN_STRINGS_RESPONSE

    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)

    skills_manager = _FakeSkillsManager()
    entry = await skill_extractor.maybe_extract_skill(
        _FakeSession(),
        skills_manager,
        endpoint_url="http://endpoint",
        model="test-model",
        headers={},
        round_count=3,
        tool_count=3,
        owner="alice",
    )

    assert entry is not None
    assert entry["title"] == "Use {braces} in config"
