r"""Regression test for ReDoS in the calendar-extract fallback regex.

CodeQL `py/redos` (#198) flagged the inline array-matcher in
`email_pollers.py` that recovers a `[{"action": ...}, ...]` JSON array from
raw LLM output (influenced by attacker-supplied email bodies). The original
pattern used `[^[\]]*?` lazy runs inside a `(...)*` repetition, which
backtracks *exponentially* on inputs like `[{"action"},{` + `}},{{` * N.

The regex is now a module-level constant so it can be pinned here. These tests
assert it (a) still extracts well-formed action arrays and (b) returns
promptly on the adversarial input that hung the old pattern.
"""

import time

from routes.email_pollers import _CAL_ACTION_ARRAY_RE


def _matches(s):
    return [m.group() for m in _CAL_ACTION_ARRAY_RE.finditer(s)]


def test_extracts_action_array_from_prose():
    s = 'Here you go:\n[{"action":"add","title":"Standup","start":"2026-07-01T09:00"}]\nThanks!'
    assert _matches(s) == ['[{"action":"add","title":"Standup","start":"2026-07-01T09:00"}]']


def test_extracts_multi_object_array():
    s = 'prose [{"action":"add","title":"A"},{"action":"cancel","uid":"x"}] tail'
    assert _matches(s) == ['[{"action":"add","title":"A"},{"action":"cancel","uid":"x"}]']


def test_no_array_returns_no_match():
    assert _matches("no array here at all") == []


def test_bracket_in_string_value_still_extracts():
    # The old `[^[\]]` class bailed on a '[' inside a value and matched nothing;
    # the linear `[^{}]` form correctly recovers the array.
    s = '[{"action":"add","title":"Meeting [urgent]","start":"x"}]'
    assert _matches(s) == [s]


def test_adversarial_input_is_fast():
    evil = '[{"action"},{' + '}},{{' * 100_000  # exploded the old exponential pattern
    start = time.perf_counter()
    _CAL_ACTION_ARRAY_RE.search(evil)
    dt = time.perf_counter() - start
    assert dt < 1.0, f"_CAL_ACTION_ARRAY_RE took {dt:.2f}s on adversarial input"
