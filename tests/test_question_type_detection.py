"""Tests for question-word detection in research query enhancement."""

from src.search.query import _detect_question_type


def test_whole_word_questions_detected():
    assert _detect_question_type("what is topological data analysis") == "what"
    assert _detect_question_type("how do transformers work") == "how"
    assert _detect_question_type("why") == "why"


def test_prefix_lookalikes_not_misclassified():
    # Regression: a bare prefix used to flag these as questions and append
    # spurious boost terms in enhance_query.
    assert _detect_question_type("whatsapp pricing") is None
    assert _detect_question_type("however we proceed") is None
    assert _detect_question_type("whole foods stock") is None
    assert _detect_question_type("howard stern show") is None
