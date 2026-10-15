"""Regression tests for scheduled-task answer collection.

Runs sometimes stream the real final answer and then a tail of empty
``final_response`` rounds (thinking-only completions). The merge used to
treat every ``final_response`` as a full replacement, so the empty tail
wiped the answer and the run finished with no output (observed on run
a3896a79 of the whats2api Conversation Analyzer).
"""

from src.task_scheduler import _merge_final_answer


def test_empty_final_response_keeps_collected_answer():
    full, replaced = _merge_final_answer("the findings", False, "")
    assert full == "the findings"
    assert replaced is False


def test_whitespace_final_response_keeps_collected_answer():
    full, replaced = _merge_final_answer("the findings", False, "   \n")
    assert full == "the findings"
    assert replaced is False


def test_nonempty_final_response_supersedes_collected_answer():
    full, replaced = _merge_final_answer("old answer", False, "sanitized answer")
    assert full == "sanitized answer"
    assert replaced is True


def test_empty_final_response_with_no_prior_text_stays_empty():
    full, replaced = _merge_final_answer("", False, "")
    assert full == ""
    assert replaced is True
