r"""Regression test for a polynomial-ReDoS sink in `_parse_qwen3_native_text_call`.

The Python-like single-call fallback (`manage_notes(action="list")`) finds the
call with

    \b([A-Za-z_][\w:.-]*)\s*\(([^()]*)\)

`.`, `-` and `:` are matched by the name's character class and are also word
boundaries, so in a long run of them (`a.a.a.a...`, `a-a-a-a...`, `a:a:a:a...`)
every boundary starts a fresh attempt that scans to the end of the run before
failing when no `(` follows. That is O(n^2): about 12 s for a 40 KB run. The
function runs over every agent response, which is untrusted model output.

The name is now length-bounded. These tests pin:
  * legitimate calls are still parsed exactly as before, and
  * a long dotted / hyphenated / colon-separated run completes promptly.

The timing bound is loose (seconds) so it never flakes on a slow CI box: the
unbounded pattern took ~12 s on these inputs and the bounded one ~0.2 s.
"""

import time

import pytest

import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import _parse_qwen3_native_text_call, parse_tool_blocks

_BUDGET_S = 4.0


def _timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


# -- correctness is preserved -------------------------------------------------

def test_plain_call_still_parsed():
    block = _parse_qwen3_native_text_call('manage_notes(action="list")')
    assert block is not None
    assert block.tool_type == "manage_notes"
    assert '"action": "list"' in block.content


def test_call_after_prose_still_parsed():
    block = _parse_qwen3_native_text_call('Sure, I will check.\nweb_search(query="rust traits")')
    assert block is not None
    assert (block.tool_type, block.content) == ("web_search", "rust traits")


def test_call_after_numbered_list_marker_still_parsed():
    # `1.` is not part of the name (it must start with a letter), so the call
    # begins at the next word boundary.
    block = _parse_qwen3_native_text_call('1.web_search(query="x")')
    assert block is not None
    assert (block.tool_type, block.content) == ("web_search", "x")


def test_namespaced_mcp_name_still_parsed():
    block = _parse_qwen3_native_text_call("mcp__srv__tool.name(a=1)")
    assert block is not None
    assert block.tool_type == "mcp__srv__tool.name"


def test_dotted_receiver_is_still_not_a_tool_call():
    assert _parse_qwen3_native_text_call('obj.web_search(query="x")') is None


def test_implausibly_long_name_is_not_a_tool_call():
    assert _parse_qwen3_native_text_call("web_search" + "-" * 400 + '(query="x")') is None


# -- pathological runs complete promptly --------------------------------------

@pytest.mark.parametrize("unit", [".a", "a-", "a:"])
def test_long_boundary_run_is_fast(unit):
    text = unit * 20000
    _, elapsed = _timed(_parse_qwen3_native_text_call, text)
    assert elapsed < _BUDGET_S


@pytest.mark.parametrize("unit", [".a", "a-", "a:"])
def test_long_boundary_run_is_fast_through_parse_tool_blocks(unit):
    text = unit * 20000
    blocks, elapsed = _timed(parse_tool_blocks, text)
    assert blocks == []
    assert elapsed < _BUDGET_S
