"""Regression tests for ReDoS in agent_loop's `<think>...</think>` stripping.

CodeQL flagged `py/polynomial-redos` on the lazy `<think>.*?</think>` pattern
used in `src/agent_loop.py` (one compiled `_THINK_RE`, one inline copy). It is
applied with `re.sub` over a whole model response. When the closing delimiter
is missing, the engine rescans to end-of-string from every `<think>` opener ->
O(n^2) on attacker-influenced input (prompt injection via tool output /
retrieved content echoed back by the model).

The fix replaces the regex with `_strip_think_blocks`, a forward-only linear
scan that is byte-for-byte equivalent to the original
`re.sub(r'<think>.*?</think>', '', text, flags=DOTALL|IGNORECASE)`.

These tests pin BOTH halves:
  * output is identical to the reference regex for legitimate inputs, and
  * pathological "many openers, no closer" input completes promptly.
"""

import re
import time

from src.agent_loop import _strip_think_blocks

# The exact pattern this fix replaces. Used only as an equivalence oracle on
# well-formed inputs (never on the adversarial one, where it is the slow path).
_REFERENCE_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _reference(text: str) -> str:
    return _REFERENCE_RE.sub("", text or "")


# Loose ceiling: the linear helper finishes in well under 100ms; the vulnerable
# regex took seconds-to-tens-of-seconds on the same input.
_BUDGET_S = 4.0


# -- equivalence with the original regex -------------------------------------

EQUIV_CASES = [
    "",
    "no tags here at all",
    "<think>hidden</think>visible",
    "before<think>cot</think>after",
    "a<think>one</think>b<think>two</think>c",
    "<think>only</think>",
    "<think></think>tail",
    "<think>a<think>nested</think>rest",          # lazy stops at first closer
    "leading</think>orphan<think>x</think>",      # orphan closer is NOT stripped
    "trailing<think>no closer for this one",       # dangling opener kept verbatim
    "CASE <THINK>UP</THINK> mix <Think>x</Think>",  # case-insensitive
    "multi\nline\n<think>a\nb\nc</think>\nkeep",   # DOTALL across newlines
    "<thinking>not matched by narrow regex</thinking>",  # only literal <think>
    "<think >space-in-tag not matched</think >",   # literal tag only
]


def test_strip_think_blocks_matches_reference_regex():
    for case in EQUIV_CASES:
        assert _strip_think_blocks(case) == _reference(case), repr(case)


def test_empty_and_none_safe():
    assert _strip_think_blocks("") == ""
    assert _strip_think_blocks(None) in (None, "")


# -- ReDoS bound -------------------------------------------------------------

def test_many_openers_no_closer_is_linear():
    # Attacker echoes thousands of "<think>" with no closer. The lazy regex
    # rescans to EOS from each opener (O(n^2)); the helper scans once.
    hostile = "<think>" * 60_000 + "x"
    start = time.perf_counter()
    out = _strip_think_blocks(hostile)
    elapsed = time.perf_counter() - start
    # No closer anywhere -> nothing is stripped, input returned intact.
    assert out == hostile
    assert elapsed < _BUDGET_S, f"took {elapsed:.2f}s (expected linear)"


def test_openers_then_one_far_closer_is_linear():
    hostile = "<think>" * 60_000 + "</think>" + "tail"
    start = time.perf_counter()
    out = _strip_think_blocks(hostile)
    elapsed = time.perf_counter() - start
    # First opener pairs with the single closer; lazy match spans to it.
    assert out == "tail"
    assert elapsed < _BUDGET_S, f"took {elapsed:.2f}s (expected linear)"
