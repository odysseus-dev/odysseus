"""Regression: builtin_actions heuristics must tolerate non-string input.

_result_has_work did `result.lower()` after a falsy-only guard, and
_classify_event_heuristic did `(summary or "").lower()`; a truthy non-string
(e.g. a dict) raised AttributeError. They now coerce/guard non-strings.
"""
from src.builtin_actions import _result_has_work, _classify_event_heuristic


def test_result_has_work_non_string():
    assert _result_has_work({"x": 1}) is False
    assert _result_has_work(123) is False


def test_classify_event_heuristic_non_string():
    out = _classify_event_heuristic(123)
    assert isinstance(out, tuple)


def test_valid_inputs_unchanged():
    assert _result_has_work("Processed 0 emails") is False
