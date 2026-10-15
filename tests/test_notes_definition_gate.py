#!/usr/bin/env python3
"""The notes-definition shortcut must not eat a prompt's tool calls.

``_notes_general_definition_answer`` exists so a person typing "what is a
note?" in chat gets a sentence instead of a bogus saved note. It had no
shape check on its *input*, so any long instruction prompt containing
"what", "explain" or "meaning" matched it — and the caller then discards
the model's tool calls and substitutes the dictionary sentence.

The WhatsApp Conversation Analyzer hit this on every single run. Its
manifest contains "intervals already analyzed, with **what** was
persisted", so each run threw away the note the model had just written
about a real WhatsApp message and answered "A note can mean a short
written record, a comment, or a musical pitch" instead.

Silent: the run still reports success, the tool was logged as accepted,
and only the artifact was missing.

Run with::

    PYTHONPATH=/Users/maosone/Documents/odysseus python3 tests/test_notes_definition_gate.py
"""

from __future__ import annotations

import sys

from src.agent_loop import _notes_general_definition_answer as gate

_failures: list = []


def ok(msg: str) -> None:
    print(f"  [ok] {msg}")


def bad(msg: str) -> None:
    _failures.append(msg)
    print(f"  [FAIL] {msg}")


def test_real_chat_questions_still_work():
    """The behaviour the shortcut was written for must not regress."""
    for q in (
        "What is a musical note?",
        "what does pinned mean",
        "define tag",
        "what is a checklist",
        "explain archive",
    ):
        if not gate(q):
            bad(f"a short definition question stopped being answered: {q!r}")
            return
    ok("short definition questions are still answered")


def test_instruction_prompts_are_never_treated_as_questions():
    """The bug: a manifest that merely mentions 'what'."""
    analyzer_manifest = """# WhatsApp Conversation Analyzer

## Scope (every run)
- **You analyze NEW conversation intervals only.** Your FIRST call must be
  `mcp__oxs_whats2api_bridge__get_analysis_queue`.
  - `done_intervals` — intervals already analyzed, with what was persisted.

## Hard rules
1. **NEVER call** `send_message`, `bash`.
2. **No fabrication.** If a deadline is vague ("em breve"), do not create
   a task. Explain nothing; persist.
"""

    if gate(analyzer_manifest):
        bad("a long instruction prompt was answered as a definition question")
        return
    ok("analyzer-style manifest is not treated as a definition question")


def test_structural_markers_are_enough_to_reject():
    cases = {
        "bulleted list": "- what is a note\n- another line",
        "heading": "# Notes\nwhat does pinned mean",
        "numbered": "1. what is a note\n2. explain",
        "backticks": "persist with `manage_notes` what you saw",
        "bold": "**what** was persisted",
    }
    for label, text in cases.items():
        if gate(text):
            bad(f"{label} was treated as a definition question: {text!r}")
            return
    ok("bullets, headings, backticks and bold all reject the shortcut")


def test_length_bound_is_not_a_bug_of_its_own():
    """A long question that is genuinely about notes still passes the gate's
    own trigger, so this asserts the bound is what saves us, not the regex."""
    long_but_bulleted = ("what is a note? " + "- filler line\n" * 40)
    if gate(long_but_bulleted):
        bad("a long bulleted prompt slipped through")
        return
    ok("the length/shape bound catches what the word list cannot")


def main() -> int:
    print("notes-definition gate\n")
    for test in (
        test_real_chat_questions_still_work,
        test_instruction_prompts_are_never_treated_as_questions,
        test_structural_markers_are_enough_to_reject,
        test_length_bound_is_not_a_bug_of_its_own,
    ):
        test()
    if _failures:
        print(f"\n{len(_failures)} failure(s)")
        return 1
    print("\nAll notes-gate checks PASS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())