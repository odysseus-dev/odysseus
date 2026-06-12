"""Regression: _strip_reasoning_prose must not destroy the answer.

It kept the text AFTER the *last* reasoning paragraph. When a reasoning-style
sentence trailed the real answer, `keep` became empty and the function returned
that trailing sentence (`paragraphs[-1]`), discarding the actual answer above
it. It now strips only a leading contiguous run of reasoning paragraphs.
"""
from src.text_helpers import strip_think


def test_leading_reasoning_is_stripped():
    out = strip_think("I need to draft a reply.\n\nThe answer is 42.", prose=True)
    assert out == "The answer is 42."


def test_trailing_reasoning_does_not_destroy_answer():
    text = ("Dear Alice,\n\nI will send the report by Friday.\n\nBest, Bob"
            "\n\nI need to keep this reply concise and professional.")
    out = strip_think(text, prose=True)
    assert "send the report by Friday" in out
    assert "Dear Alice" in out


def test_plain_text_unchanged():
    assert strip_think("Just a normal answer.", prose=True) == "Just a normal answer."
