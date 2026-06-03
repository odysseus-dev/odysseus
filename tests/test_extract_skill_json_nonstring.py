"""Regression: _extract_skill_json must tolerate a non-string response.

The `if not teacher_response` guard only handled falsy values; a truthy
non-string (e.g. a number or list from an unexpected LLM client) reached
`re.search(..., teacher_response)` and raised TypeError. Non-strings now
return None (treated as "no skill"), matching the documented contract.
"""
from src.teacher_escalation import _extract_skill_json


def test_non_string_returns_none():
    assert _extract_skill_json(123) is None
    assert _extract_skill_json(["x"]) is None
    assert _extract_skill_json(None) is None


def test_valid_json_block_parsed():
    resp = "sure:\n```json\n{\"name\": \"x\"}\n```\n"
    assert _extract_skill_json(resp) == {"name": "x"}
