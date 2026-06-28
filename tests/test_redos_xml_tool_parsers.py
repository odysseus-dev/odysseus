"""Regression tests for the remaining ReDoS sinks in tool_parsing.py.

A previous fix (test_redos_llm_parsers.py) hardened the delimiter-bounded
[TOOL_CALL]/<tool_call>/<tool_code> scanners but explicitly left four patterns
that CodeQL (py/polynomial-redos) flagged on the next rescan:

  * `args => { ... }` in `_parse_tool_call_block` — greedy `\\{([\\s\\S]*)\\}`
    that `re.search` restarts from every `args:{` opener -> O(n^2).
  * `_XML_INVOKE_RE` — lazy `<invoke ...>([\\s\\S]*?)</invoke>` that rescans to
    end-of-string from every opener when no `</invoke>` follows.
  * `_XML_DIRECT_TOOL_RE` and the `<tag>([\\s\\S]*?)</\\1>` param scan in
    `_parse_tool_code_block` — lazy *backreference* patterns with the same
    opener-flood blowup.

These run over untrusted model output (tool-call markup is attacker-influenced
via prompt injection), so each is now a forward-only scan. The tests pin:
  * correctness is unchanged for legitimate tool-call markup, and
  * pathological "many openers, no closer" inputs complete promptly.

The timing bound is loose (seconds) so it never flakes on a slow CI box; the
unguarded patterns took 2-15s on these inputs, so the margin is ~100x.
"""

import time

import pytest

import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import (
    parse_tool_blocks,
    strip_tool_blocks,
    _parse_tool_call_block,
    _parse_tool_code_block,
)

_BUDGET_S = 4.0


def _timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


# ── correctness is preserved ────────────────────────────────────────────────

def test_xml_invoke_call_still_parsed():
    blocks = parse_tool_blocks(
        '<tool_call><invoke name="bash"><parameter name="command">ls -la</parameter></invoke></tool_call>'
    )
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "ls -la")]


def test_xml_direct_tool_still_parsed():
    blocks = parse_tool_blocks('<tool_call><web_search>weather today</web_search></tool_call>')
    assert [(b.tool_type, b.content) for b in blocks] == [("web_search", "weather today")]


def test_xml_direct_tool_backref_is_case_insensitive():
    # `</\\1>` matched case-insensitively under re.IGNORECASE; the forward-only
    # scanner preserves that (mixed-case closer still pairs with its opener).
    blocks = parse_tool_blocks('<tool_call><Web_Search>q</WEB_SEARCH></tool_call>')
    assert [(b.tool_type, b.content) for b in blocks] == [("web_search", "q")]


def test_tool_code_xml_params_still_parsed():
    blocks = parse_tool_blocks("<tool_code>{tool => 'bash', args => '<command>ls -la</command>'}</tool_code>")
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "ls -la")]


def test_xml_invoke_multiple_parameters_still_parsed():
    # The invoke parameter scan is forward-only; a well-formed invoke with more
    # than one <parameter> must still yield every name/value pair.
    blocks = parse_tool_blocks(
        '<tool_call><invoke name="web_search">'
        '<parameter name="query">rust traits</parameter>'
        '<parameter name="time_filter">week</parameter>'
        '</invoke></tool_call>'
    )
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert '"query": "rust traits"' in blocks[0].content
    assert '"time_filter": "week"' in blocks[0].content


def test_xml_direct_distinct_tag_names_still_parsed():
    # Distinct sibling tags inside <tool_call> each pair with their own closer;
    # the forward-only direct scan must keep matching after the first block.
    blocks = parse_tool_blocks(
        '<tool_call><web_search>weather</web_search><read_file>notes.txt</read_file></tool_call>'
    )
    assert [(b.tool_type, b.content) for b in blocks] == [
        ("web_search", "weather"),
        ("read_file", "notes.txt"),
    ]


def test_tool_call_args_brace_still_parsed():
    blocks = parse_tool_blocks('[TOOL_CALL]{tool => "shell", args => {--command "ls"}}[/TOOL_CALL]')
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "ls")]


def test_args_brace_takes_through_last_close_brace():
    # `\\{([\\s\\S]*)\\}` is greedy to the LAST `}`; the rfind-based rewrite must
    # match that (keep the nested object intact, not stop at the first `}`).
    block = _parse_tool_call_block('tool => "bash", args => {--command "echo {x} done"}')
    assert block is not None and block.tool_type == "bash"
    assert block.content == "echo {x} done"


def test_fenced_invoke_still_parsed():
    blocks = parse_tool_blocks(
        '```python\n<invoke name="bash"><parameter name="command">whoami</parameter></invoke>\n```'
    )
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "whoami")]


# ── pathological inputs no longer blow up ───────────────────────────────────

def test_args_brace_opener_flood_is_fast():
    # Many `args:{` openers, no closing `}` — old greedy capture restarted from
    # every opener (>10s); the bounded opener + rfind is O(n).
    evil = "args:{{a" * 14000
    block, dt = _timed(_parse_tool_call_block, evil)
    assert dt < _BUDGET_S, f"_parse_tool_call_block took {dt:.2f}s"
    assert block is None
    # And through the public path, wrapped in a [TOOL_CALL] block.
    _, dt2 = _timed(parse_tool_blocks, "[TOOL_CALL]{" + evil + "}[/TOOL_CALL]")
    assert dt2 < _BUDGET_S, f"parse_tool_blocks took {dt2:.2f}s"


def test_xml_invoke_opener_flood_is_fast():
    # Bare <invoke> opener flood, no </invoke> closer.
    evil = ('<invoke name="x">' + "a" * 10) * 6000
    blocks, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"
    assert blocks == []


def test_xml_invoke_stale_closer_before_opener_flood_is_fast():
    # A lone leading </invoke> satisfies a substring guard, but no opener after
    # it has a reachable closer.
    evil = "</invoke>" + ('<invoke name="x">' + "a" * 10) * 6000
    _, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"


def test_xml_direct_backref_opener_flood_is_fast():
    # <tool_call> wrapper (no </tool_call>) routes into the open-wrapper path,
    # which reaches the _XML_DIRECT_TOOL_RE backreference scan: a `<a><a>...`
    # flood with no `</a>` closer.
    evil = "<tool_call>" + "<a><a>b" * 6000
    blocks, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"
    assert blocks == []


def test_tool_code_param_backref_flood_is_fast():
    # `<x><x>...` param flood inside tool_code args, no `</x>` closer — exercises
    # the `<tag>([\\s\\S]*?)</\\1>` backreference scan in _parse_tool_code_block.
    args_flood = "tool => 'bash', args => " + "<x><x>a" * 6000
    block, dt = _timed(_parse_tool_code_block, args_flood)
    assert dt < _BUDGET_S, f"_parse_tool_code_block took {dt:.2f}s"
    # Through the public path, inside a closed <tool_code> block.
    _, dt2 = _timed(parse_tool_blocks, "<tool_code>{" + args_flood + "}</tool_code>")
    assert dt2 < _BUDGET_S, f"parse_tool_blocks took {dt2:.2f}s"


def test_xml_invoke_closed_with_parameter_opener_flood_is_fast():
    # A CLOSED <invoke> whose body is a flood of `<parameter name=..>` openers
    # with no `</parameter>` closer: the invoke delimiter pairs fine, but the
    # inner parameter scan must not rescan the body from every opener (O(n^2)).
    evil = ('<tool_call><invoke name="bash">'
            + '<parameter name="x">' * 6000
            + '</invoke></tool_call>')
    blocks, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"
    # No `</parameter>` ever closes, so no params are captured.
    assert len(blocks) == 1 and blocks[0].tool_type == "bash"


def test_xml_direct_distinct_name_opener_flood_is_fast():
    # Distinct unclosed tag names (`<t0><t1>...`) defeat per-name memoization;
    # the scan must still stay near-linear instead of searching the suffix once
    # per new name.
    evil = "<tool_call>" + "".join(f"<t{i}>" for i in range(45000))
    blocks, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"
    assert blocks == []


def test_tool_code_param_distinct_name_flood_is_fast():
    # Same distinct-name flood inside tool_code args, reaching the param backref
    # scan in _parse_tool_code_block.
    args_flood = "tool => 'bash', args => " + "".join(f"<t{i}>" for i in range(45000))
    _, dt = _timed(_parse_tool_code_block, args_flood)
    assert dt < _BUDGET_S, f"_parse_tool_code_block took {dt:.2f}s"
