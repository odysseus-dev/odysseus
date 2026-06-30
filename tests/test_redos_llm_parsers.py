"""Regression tests for ReDoS in the regexes that parse untrusted LLM output.

CodeQL flagged several `py/polynomial-redos` sinks in `text_helpers.py` and
`tool_parsing.py`. Each is a delimiter-bounded pattern (`<open>...<close>`)
applied with `re.sub`/`re.finditer` over a whole model response. When the
closing delimiter is missing, the engine rescans to end-of-string from every
opening occurrence -> O(n^2) on attacker-influenced input (prompt injection
via tool output / retrieved content).

These tests pin BOTH halves of the fix:
  * correctness is unchanged for legitimate inputs, and
  * pathological "many openers, no closer" inputs complete promptly.

The timing bound is deliberately loose (seconds, not ms) so it never flakes on
a slow CI box; the unguarded code took tens of seconds on the same inputs, so
the margin is ~100x.
"""

import time

import pytest

import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.text_helpers import normalize_thinking_markup, strip_think
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks

# Loose ceiling: guarded paths finish in well under 100ms; the vulnerable
# versions took 8-30s on these same inputs.
_BUDGET_S = 4.0


def _timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


# ── correctness is preserved ────────────────────────────────────────────────

def test_thought_attr_normalization_unchanged():
    # `<thought time="0.4">` -> `<think time="0.4">` then stripped.
    assert strip_think('<thought time="0.4">reasoning</thought>Answer.') == "Answer."
    assert normalize_thinking_markup("<thought>x</thought>") == "<think>x</think>"


def test_gemma_channel_unwrap_unchanged():
    text = "<|channel>thought\ninternal<channel|><|channel>response\nFinal.<channel|>"
    assert strip_think(text) == "Final."


def test_thought_prefix_tags_not_overmatched():
    # The `<thought...>` opener must keep a tag-name boundary: tags whose names
    # merely start with "thought" are unrelated markup and must pass through
    # untouched (no `<thinkful>`/`<thinks>` corruption).
    for text in ("<thoughtful>keep</thoughtful>", "<thoughts>keep</thoughts>"):
        assert normalize_thinking_markup(text) == text


def test_tool_call_blocks_still_parsed():
    blocks = parse_tool_blocks('[TOOL_CALL]{tool: "shell", command: "ls"}[/TOOL_CALL]')
    assert blocks, "well-formed [TOOL_CALL] block should still parse"
    assert "[TOOL_CALL]" not in strip_tool_blocks('before [TOOL_CALL]{tool: "shell", command: "ls"}[/TOOL_CALL] after')


def test_xml_tool_call_blocks_still_parsed():
    xml = '<tool_call><invoke name="bash"><parameter name="command">ls</parameter></invoke></tool_call>'
    blocks = parse_tool_blocks(xml)
    assert blocks, "well-formed <tool_call> block should still parse"
    assert "tool_call" not in strip_tool_blocks(xml)


def test_tool_code_blocks_still_parsed():
    assert "<tool_code>" not in strip_tool_blocks('<tool_code>{"tool": "shell"}</tool_code>')


# ── pathological inputs no longer blow up ───────────────────────────────────

def test_thought_open_no_close_is_fast():
    evil = "<thought" + " " * 60_000  # no closing '>', ambiguous (\s+[^>]*)? loops
    out, dt = _timed(normalize_thinking_markup, evil)
    assert dt < _BUDGET_S, f"normalize_thinking_markup took {dt:.2f}s"
    assert out == evil  # nothing to normalize, returned unchanged


def test_gemma_channel_opener_flood_is_fast():
    evil = "<|channel>thought\n" * 4000  # no <channel|> closer
    _, dt = _timed(normalize_thinking_markup, evil)
    assert dt < _BUDGET_S, f"normalize_thinking_markup took {dt:.2f}s"


def test_gemma_stale_closer_before_opener_flood_is_fast():
    # A lone leading <channel|> makes a whole-string "closer present?" check
    # true, but no <|channel>thought opener after it has a reachable closer.
    evil = "<channel|>" + "<|channel>thought\n" * 4000
    _, dt = _timed(normalize_thinking_markup, evil)
    assert dt < _BUDGET_S, f"normalize_thinking_markup took {dt:.2f}s"


def test_tool_call_opener_flood_is_fast():
    evil = "[TOOL_CALL]{tool: x}" * 6000  # '}' present but no [/TOOL_CALL] closer
    blocks, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"
    assert blocks == []
    _, dt2 = _timed(strip_tool_blocks, evil)
    assert dt2 < _BUDGET_S, f"strip_tool_blocks took {dt2:.2f}s"


def test_xml_tool_call_opener_flood_is_fast():
    # strip_tool_blocks exercises the CodeQL-flagged _XML_TOOL_CALL_RE in
    # isolation (the parse path also reaches _XML_DIRECT_TOOL_RE, a separate
    # unflagged backreference pattern tracked as a follow-up).
    evil = ("<tool_call>" + "a" * 20) * 4000  # no </tool_call> closer
    _, dt = _timed(strip_tool_blocks, evil)
    assert dt < _BUDGET_S, f"strip_tool_blocks took {dt:.2f}s"


def test_tool_code_opener_flood_is_fast():
    evil = "<tool_code>{tool: x}" * 6000  # '}' present but no </tool_code> closer
    _, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"
    _, dt2 = _timed(strip_tool_blocks, evil)
    assert dt2 < _BUDGET_S, f"strip_tool_blocks took {dt2:.2f}s"


# ── a present closer must not re-enable the O(n^2) rescan ────────────────────
# A whole-string "closer exists?" guard is defeated by a stale closer placed
# before an opener flood, or by a closer whose required inner delimiter is
# missing. The parser must pair each opener only with a *later* closer.

def test_xml_stale_closer_before_opener_flood_is_fast():
    # A lone leading </tool_call> makes a whole-string closer check true, but no
    # opener after it has a reachable closer. (strip exercises the CodeQL-flagged
    # _XML_TOOL_CALL_RE path; parse additionally reaches _XML_DIRECT_TOOL_RE, the
    # separate backreference pattern tracked as a follow-up — see
    # test_xml_tool_call_opener_flood_is_fast.)
    evil = "</tool_call>" + ("<tool_call>" + "a" * 10) * 6000
    _, dt = _timed(strip_tool_blocks, evil)
    assert dt < _BUDGET_S, f"strip_tool_blocks took {dt:.2f}s"


def test_tool_call_closer_present_without_inner_brace_is_fast():
    # Leading [/TOOL_CALL] satisfies a substring guard, but the openers carry no
    # inner '}', so '}\\s*[/TOOL_CALL]' is never reachable from any opener.
    evil = "[/TOOL_CALL]" + "[TOOL_CALL]{tool: x" * 6000
    blocks, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"
    assert blocks == []
    _, dt2 = _timed(strip_tool_blocks, evil)
    assert dt2 < _BUDGET_S, f"strip_tool_blocks took {dt2:.2f}s"


def test_tool_code_closer_present_without_inner_brace_is_fast():
    evil = "</tool_code>" + "<tool_code>{tool: x" * 6000
    blocks, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"
    assert blocks == []
    _, dt2 = _timed(strip_tool_blocks, evil)
    assert dt2 < _BUDGET_S, f"strip_tool_blocks took {dt2:.2f}s"


# ── strip_think() is the production entrypoint that callers actually run ─────
# The timing tests above cover normalize_thinking_markup and the scanners;
# these cover strip_think() itself, which applies the think-tag regexes too.

def test_strip_think_nested_and_attr_blocks_unchanged():
    # Values pin pre-existing behavior (incl. the nested-block quirk that leaves
    # the inter-tag `c`) so the forward-only rewrite stays byte-equal.
    assert strip_think("<think>a<think>b</think>c</think>Answer.") == "cAnswer."
    assert strip_think('<think time="0.4">reasoning</think>Answer.') == "Answer."
    assert strip_think("<thinking>x</thinking>Answer.") == "Answer."
    assert strip_think("<think>r</think>Answer.") == "Answer."
    assert strip_think("Answer.") == "Answer."


def test_strip_think_malformed_open_no_gt_is_fast():
    for opener in ("<think", "<thinking", "<thought"):
        evil = opener + " " * 40_000  # no closing '>'
        out, dt = _timed(strip_think, evil)
        assert dt < _BUDGET_S, f"strip_think({opener!r}) took {dt:.2f}s"
        assert out == evil.strip()  # nothing is a real tag


def test_strip_think_attr_opener_flood_is_fast():
    for opener in ("<think x", "<thinking x", "<thought x"):  # no `>`, no closer
        evil = opener * 8000
        _, dt = _timed(strip_think, evil)
        assert dt < _BUDGET_S, f"strip_think({opener!r}) took {dt:.2f}s"


def test_strip_think_closed_opener_flood_is_fast():
    evil = "<think>" * 16000  # well-formed openers, no closer
    out, dt = _timed(strip_think, evil)
    assert dt < _BUDGET_S, f"strip_think took {dt:.2f}s"
    assert out == ""


def test_strip_think_malformed_closer_flood_is_fast():
    evil = "</think x" * 8000  # closer flood, no `>`
    out, dt = _timed(strip_think, evil)
    assert dt < _BUDGET_S, f"strip_think took {dt:.2f}s"
    assert out == evil.strip()
