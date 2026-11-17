"""Regression tests for untrusted selected-note ID parsing."""
import json
import pytest
from fastapi import HTTPException
from routes.chat_routes import _parse_selected_note_ids


@pytest.mark.parametrize("raw,expected", [
    (None, []),
    ("", []),
    ("[]", []),
    ('["one", "two", "one"]', ["one", "two"]),
])
def test_selected_note_ids_valid(raw, expected):
    assert _parse_selected_note_ids(raw) == expected


@pytest.mark.parametrize("raw", [
    "42", "true", "null", '{"id":"a"}',
    '["one", 5]', '["one", null]', '["one", false]',
    '[""]', '[" "]', '["one", {}]',
    "not json", json.dumps(["a"] * 51),
    json.dumps(["a" * 129]), " " * 8193,
])
def test_selected_note_ids_rejected(raw):
    with pytest.raises(HTTPException) as exc:
        _parse_selected_note_ids(raw)
    assert exc.value.status_code == 400
