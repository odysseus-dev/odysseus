"""[bash]/[shell]/[python]...[/same-tag] square-bracket tool-call parsing (#5187).

Qwen-family finetunes are trained to emit the OpenAI function-call object as
JSON inside a <tool_call> wrapper (already handled elsewhere), but some
finetunes/quantizations drift to a simpler bracket form instead:

    [bash]mkdir -p agent-test[/bash]

In text mode (manually added Ollama endpoints, native_tools=False) this used
to land as inert text and nothing executed — the model "talks but never does
anything." parse_tool_blocks had no pattern for it at all.

The fix is deliberately scoped to a small allowlist (bash/shell/python), not
the full tool-name alias map: a bare [word]...[/word] scan over generic words
like "notes"/"settings"/"run" would risk misreading ordinary bracketed prose
or markdown labels as a tool call. test_bracket_tag_does_not_match_prose pins
that guard.
"""
import time

import src.agent_tools  # noqa: F401  (break agent_tools<->tool_parsing import cycle)
from src.tool_parsing import parse_tool_blocks, strip_tool_blocks

_BUDGET_S = 4.0


def _timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


# ── correctness ──────────────────────────────────────────────────────────
def test_bash_bracket_tag_parses_and_strips():
    raw = "[bash]mkdir -p agent-test[/bash]"
    blocks = parse_tool_blocks(raw)
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "mkdir -p agent-test")]
    assert strip_tool_blocks(raw).strip() == ""


def test_python_bracket_tag_multiline_body_parses():
    raw = "[python]\nimport os\nprint(1)\n[/python]"
    blocks = parse_tool_blocks(raw)
    assert [(b.tool_type, b.content) for b in blocks] == [("python", "import os\nprint(1)")]


def test_shell_alias_normalizes_to_bash():
    blocks = parse_tool_blocks("[shell]ls -la[/shell]")
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "ls -la")]


def test_bracket_tag_is_case_insensitive():
    blocks = parse_tool_blocks("[Bash]echo hi[/Bash]")
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "echo hi")]


def test_multiple_bracket_calls_in_one_turn_all_parse():
    raw = "[bash]mkdir -p x[/bash]\n[python]print(1)[/python]"
    blocks = parse_tool_blocks(raw)
    assert [(b.tool_type, b.content) for b in blocks] == [
        ("bash", "mkdir -p x"),
        ("python", "print(1)"),
    ]


def test_strip_removes_bracket_markup_but_keeps_surrounding_prose():
    raw = "Sure, running it now.\n[bash]pwd[/bash]\nDone."
    cleaned = strip_tool_blocks(raw)
    assert "[bash]" not in cleaned and "[/bash]" not in cleaned
    assert "Sure, running it now." in cleaned
    assert "Done." in cleaned


def test_unclosed_bracket_tag_is_ignored_not_executed():
    # No closer at all -> nothing to execute, and nothing crashes.
    raw = "[bash]mkdir -p agent-test"
    assert parse_tool_blocks(raw) == []


def test_empty_bracket_body_is_not_executed():
    assert parse_tool_blocks("[bash][/bash]") == []


# ── false-positive guard: the reason the tag set is a narrow allowlist ─────
def test_bracket_tag_does_not_match_prose():
    raw = "Here are some [notes] to remember [/notes] about the task."
    assert parse_tool_blocks(raw) == []


def test_bracket_tag_does_not_shadow_generic_alias_words():
    # "run"/"search"/"settings" are real aliases in _TOOL_NAME_MAP but are
    # deliberately excluded from the bracket allowlist (see module docstring).
    raw = "[run]not a tool call[/run] and [settings]also not one[/settings]"
    assert parse_tool_blocks(raw) == []


def test_existing_tool_call_bracket_pattern_still_wins_when_present():
    # [TOOL_CALL]...[/TOOL_CALL] (Pattern 2) must still take priority and work
    # unaffected by the new Pattern 2b scan.
    raw = '[TOOL_CALL]{tool => "shell", args => {--command "ls"}}[/TOOL_CALL]'
    assert [(b.tool_type, b.content) for b in parse_tool_blocks(raw)] == [("bash", "ls")]


def test_fenced_example_of_bracket_syntax_in_prose_is_illustrative_only():
    # A model showing the bracket syntax as a code-fenced example (not
    # actually emitting it live) must not be executed twice or misparsed —
    # Pattern 1 (fenced) is tried first and wins since it produces blocks.
    raw = "```text\n[bash]example only[/bash]\n```"
    blocks = parse_tool_blocks(raw)
    # ```text is not a recognized tool tag, so Pattern 1 yields nothing and
    # falls through to Pattern 2b, which DOES see the bracket markup inside —
    # this is consistent with how the model would need to actually run it.
    assert [(b.tool_type, b.content) for b in blocks] == [("bash", "example only")]


# ── ReDoS safety: forward-only scan, no backtracking blowup ────────────────
def test_bracket_opener_flood_with_no_closer_is_fast():
    evil = "[bash]a" * 20000
    blocks, dt = _timed(parse_tool_blocks, evil)
    assert dt < _BUDGET_S, f"parse_tool_blocks took {dt:.2f}s"
    assert blocks == []


def test_bracket_stripper_opener_flood_with_no_closer_is_fast():
    evil = "[bash]a" * 20000
    cleaned, dt = _timed(strip_tool_blocks, evil)
    assert dt < _BUDGET_S, f"strip_tool_blocks took {dt:.2f}s"
    assert cleaned == evil.strip()
