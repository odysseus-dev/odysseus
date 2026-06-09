"""Regression: skill-extraction JSON parsing must tolerate a stray brace in prose.

maybe_extract_skill() sliced the LLM response from the first '{' to the last
'}'. When a model emits a stray brace in prose before the real object
(e.g. "uses {placeholder} then {...}"), that slice starts at the prose brace and
json.loads fails, so a perfectly good skill is silently dropped. Extraction now
tries each '{' start position and returns the first candidate that parses to a
JSON object.
"""
from services.memory import skill_extractor


def test_stray_brace_before_real_json_is_recovered():
    resp = (
        'The user mentioned {placeholder} before the actual JSON '
        '{"title": "Restart the service", "steps": ["a", "b"]}'
    )
    data = skill_extractor._extract_json_object(resp)
    assert isinstance(data, dict)
    assert data["title"] == "Restart the service"


def test_clean_json_object():
    data = skill_extractor._extract_json_object('{"title": "Y", "steps": []}')
    assert data["title"] == "Y"


def test_code_fenced_json():
    data = skill_extractor._extract_json_object('```json\n{"title": "Z"}\n```')
    assert data["title"] == "Z"


def test_no_json_object_returns_none():
    assert skill_extractor._extract_json_object("just prose, no object here") is None


def test_non_object_json_returns_none():
    # A bare array is valid JSON but not a skill object.
    assert skill_extractor._extract_json_object("[1, 2, 3]") is None


def test_empty_input_returns_none():
    assert skill_extractor._extract_json_object("") is None
