"""Regression: _detect_question_type was a bare prefix match, causing false positives.

Examples before the fix:
  'whatsapp pricing'  -> 'what'   (should be None)
  'however we proceed'-> 'how'    (should be None)
  'whole foods stock' -> 'who'    (should be None)
  'whenever we ship'  -> 'when'   (should be None)

Fix: require the question word to be the entire query or be followed by a space.
Tested against both src/search/query.py and services/search/query.py (duplicates).
"""
import pytest

from src.search.query import _detect_question_type as src_detect
from services.search.query import _detect_question_type as svc_detect


@pytest.mark.parametrize("detect", [src_detect, svc_detect], ids=["src", "services"])
class TestDetectQuestionType:
    # --- True question prefixes ---
    def test_what_is(self, detect):
        assert detect("what is the capital of France") == "what"

    def test_who_invented(self, detect):
        assert detect("who invented the telephone") == "who"

    def test_when_did(self, detect):
        assert detect("when did World War II end") == "when"

    def test_where_is(self, detect):
        assert detect("where is the Eiffel Tower") == "where"

    def test_why_does(self, detect):
        assert detect("why does the sky change color at sunset") == "why"

    def test_how_does(self, detect):
        assert detect("how does photosynthesis work") == "how"

    # --- Bare question words (exact match) ---
    def test_bare_what(self, detect):
        assert detect("what") == "what"

    def test_bare_how(self, detect):
        assert detect("how") == "how"

    # --- Prefix lookalikes — must NOT match ---
    def test_whatsapp_is_not_what(self, detect):
        assert detect("whatsapp pricing") is None

    def test_however_is_not_how(self, detect):
        assert detect("however we proceed") is None

    def test_whole_foods_is_not_who(self, detect):
        assert detect("whole foods stock") is None

    def test_whenever_is_not_when(self, detect):
        assert detect("whenever we ship") is None

    def test_wherever_is_not_where(self, detect):
        assert detect("wherever you go") is None

    def test_wholesale_is_not_who(self, detect):
        assert detect("wholesale market trends") is None

    def test_howard_stern_is_not_how(self, detect):
        assert detect("howard stern show") is None

    # --- Case insensitivity ---
    def test_uppercase_what(self, detect):
        assert detect("What time is it") == "what"

    def test_mixed_case_how(self, detect):
        assert detect("HOW do I fix this") == "how"

    # --- No question word ---
    def test_no_question_word(self, detect):
        assert detect("python list comprehension performance") is None

    def test_empty_string(self, detect):
        assert detect("") is None
