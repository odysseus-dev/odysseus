"""<function name="..."><param name="...">...</param></function> pseudo-calls.

minicpm5-2b (and other small local models) imitate the OpenAI function-calling
documentation instead of the fenced-block convention the system prompt teaches:

  The user wants me to perform a search ... which I can do with the web_search
  tool.
  <function name="web_search"><param name="query">QUERY</param></function>

No existing pattern matched that shape, so parse_tool_blocks returned [] and
the agent loop treated the message as a final answer — the tool never ran.
These tests pin: the markup parses like <invoke>/<parameter> markup (via the
canonical converter), it is stripped from display, unknown tools stay inert,
Qwen's <function=NAME> form keeps its own path, and opener floods stay fast.
"""

import json
import time

import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks

_BUDGET_S = 4.0

_REAL_TRANSCRIPT = (
    "The user wants me to perform a search of \"latest agent harness projects "
    "SOTA FOSS\". This is a\nweb search request, which I can do with the "
    "web_search tool.\n\n"
    '<function name="web_search">'
    '<param name="query">latest agent harness projects SOTA FOSS</param>'
    "</function>"
)


def test_function_param_markup_parses_as_tool_call():
    blocks = parse_tool_blocks(_REAL_TRANSCRIPT)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    # Canonical web_search shape: a lone query param stays a bare string,
    # identical to the <invoke>/<web_search> paths.
    assert blocks[0].content == "latest agent harness projects SOTA FOSS"


def test_function_param_markup_is_stripped_from_display():
    cleaned = strip_tool_blocks(_REAL_TRANSCRIPT)
    assert "web_search tool" in cleaned  # the prose stays
    assert "<function" not in cleaned
    assert "<param" not in cleaned


def test_function_param_multiple_params_and_single_quotes():
    text = (
        "<function name='web_search'>"
        "<param name='query'>q</param>"
        "<param name='time_filter'>week</param>"
        "</function>"
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert json.loads(blocks[0].content) == {"query": "q", "time_filter": "week"}


def test_function_param_unknown_tool_stays_inert():
    text = '<function name="super_secret_tool"><param name="arg1">v</param></function>'
    assert parse_tool_blocks(text) == []


def test_function_param_qwen_native_form_untouched():
    # <function=NAME> (Qwen native) must keep parsing through its own path.
    text = (
        "<tool_call><function=web_search>"
        "<parameter=query>q</parameter>"
        "</function></tool_call>"
    )
    blocks = parse_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"


def test_function_param_opener_flood_is_fast():
    # "Many openers, no closer" must not drive a lazy rescan (CodeQL
    # py/polynomial-redos); the normalizer pairs forward-only like
    # _iter_delimited.
    evil = '<function name="x">' * 6000
    start = time.perf_counter()
    assert parse_tool_blocks(evil) == []
    strip_tool_blocks(evil)
    dt = time.perf_counter() - start
    assert dt < _BUDGET_S
