"""Regression: context_compactor token helpers must tolerate non-string text.

_message_text_token_estimate and _truncate_text_to_token_budget call len(text)
on the message text; a None/non-string (e.g. an assistant tool-call message
with content=None) raised TypeError. They now coerce gracefully.
"""
from src.context_compactor import _message_text_token_estimate, _truncate_text_to_token_budget


def test_estimate_handles_non_string():
    assert _message_text_token_estimate(None) == 4
    assert _message_text_token_estimate(123) == 4


def test_truncate_handles_non_string():
    assert _truncate_text_to_token_budget(None, 1000) is None


def test_valid_text_unchanged():
    assert _message_text_token_estimate("hello") == int(len("hello") * 0.3) + 4
    assert _truncate_text_to_token_budget("short", 1000) == "short"
