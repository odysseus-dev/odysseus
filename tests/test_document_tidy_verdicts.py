"""Regression: AI-tidy verdict parsing must not crash on a malformed/prose response.

`ai_tidy_documents` extracts a JSON array of "junk"/"keep" verdicts from the LLM
response. A response containing brackets before the real array (or no valid JSON)
used to reach `json.loads()` unguarded, raising `json.JSONDecodeError` that surfaced
as a cryptic "AI tidy failed: Expecting value...". `_parse_tidy_verdicts` now returns
a clean `HTTPException(500)` instead.
"""
import pytest
from fastapi import HTTPException

from routes.document_routes import _parse_tidy_verdicts


def test_parses_valid_verdict_array():
    assert _parse_tidy_verdicts('["junk","keep","junk"]') == ["junk", "keep", "junk"]


def test_parses_array_embedded_in_prose():
    assert _parse_tidy_verdicts('Here you go: ["keep","junk"]') == ["keep", "junk"]


def test_no_array_raises_clean_500():
    with pytest.raises(HTTPException) as exc:
        _parse_tidy_verdicts("I could not classify these documents.")
    assert exc.value.status_code == 500


def test_malformed_match_raises_clean_500_not_jsondecodeerror():
    # Brackets before the real array: the non-greedy match grabs "[at the docs]",
    # which is not valid JSON. Pre-fix this raised a raw JSONDecodeError (surfaced
    # as "AI tidy failed: ..."); now it's a clean HTTPException(500).
    with pytest.raises(HTTPException) as exc:
        _parse_tidy_verdicts('I looked [at the docs] and decided: ["junk"]')
    assert exc.value.status_code == 500
