"""Regression tests for two py/polynomial-redos sinks over untrusted model text.

Both had two adjacent `\\s`-matching quantifiers that backtrack O(n^2) when the
rest of the pattern fails on a whitespace flood:

  * `routes/skills_routes.py` `_VERDICT_PROSE_RE` — `["\\'\\s:]*\\s*` (the class
    already matches `\\s`) over a teacher/verifier model's prose verdict.
  * `src/agent_loop.py` `_EXPLICIT_CONTINUATION_RE` — `\\s*[.!?]*\\s*$` over a
    user's terse reply.

Each is rewritten to drop the adjacency while keeping the exact match set. The
tests pin correctness (matches unchanged) and bound the flood inputs; the old
patterns took seconds, the loose budget is seconds, so the margin is ~100x.
"""

import time

import pytest

import src.agent_tools  # noqa: F401  (break agent_tools<->agent_loop import cycle)
from routes.skills_routes import _VERDICT_PROSE_RE
from src.agent_loop import _EXPLICIT_CONTINUATION_RE, _is_explicit_continuation

_BUDGET_S = 4.0


def _timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


# ── #229 verdict-from-prose: matches unchanged ──────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ('verdict": "FAIL"', "fail"),
    ("verdict needs_work", "needs_work"),
    ("Verdict:   inconclusive", "inconclusive"),
    ("verdict\t\t'pass'", "pass"),
    ("verdictpass", "pass"),  # all separators optional — keyword may abut, as before
    ("the verdict is: pass overall", None),  # intervening "is" breaks the run
    ("no clear decision here", None),
])
def test_verdict_prose_extraction(text, expected):
    m = _VERDICT_PROSE_RE.search(text)
    assert (m.group(1).lower() if m else None) == expected


def test_verdict_prose_flood_is_fast():
    evil = "verdict" + "\t" * 40000 + "x"  # `verdict` then whitespace, no keyword
    (m, dt) = _timed(_VERDICT_PROSE_RE.search, evil)
    assert dt < _BUDGET_S, f"_VERDICT_PROSE_RE took {dt:.2f}s"
    assert m is None


# ── #472 explicit-continuation: classification unchanged ────────────────────

@pytest.mark.parametrize("text", [
    "yes", "y", "ok!", "okay ...", "sure!!", "do it", "1", "a", "2.",
    "the second one", "  yes  ", "continue", "run it!", "third???",
])
def test_continuation_accepts_terse_confirmations(text):
    assert _is_explicit_continuation(text)


@pytest.mark.parametrize("text", [
    "no", "maybe yes", "yesx", "let's not", "y . ! .", "", "run the script please",
])
def test_continuation_rejects_non_confirmations(text):
    assert not _is_explicit_continuation(text)


def test_continuation_flood_is_fast():
    evil = "y" + "\t" * 40000 + "x"  # terse opener then whitespace flood, no `$`
    (_, dt) = _timed(_is_explicit_continuation, evil)
    assert dt < _BUDGET_S, f"_is_explicit_continuation took {dt:.2f}s"
    # Direct on the compiled pattern too (the function strips first).
    (m, dt2) = _timed(_EXPLICIT_CONTINUATION_RE.match, evil)
    assert dt2 < _BUDGET_S, f"_EXPLICIT_CONTINUATION_RE took {dt2:.2f}s"
    assert m is None
