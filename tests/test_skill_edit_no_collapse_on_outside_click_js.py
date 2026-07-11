"""Regression guard for issue #4002 — clicking the card body outside the
edit textarea collapsed the skill card and silently discarded unsaved edits.

In Brain > Skills, the card's click handler toggles expand/collapse. The
edit <textarea> stops propagation only for clicks landing ON the textarea,
so a click on the surrounding card padding bubbled up to the card handler
and collapsed the card mid-edit — losing the user's changes. The fix bails
out of the card click handler while a `.skill-md-editor` is present, so the
card only leaves edit mode via Save (or the Cancel button added in #3580).

skills.js pulls in browser globals (DOM), so it can't be imported under
node; this guards the fix at the source level so it can't be silently
dropped. Both the user-skill card (`_expandSkillCard`) and the built-in
capability card (`_expandBuiltinCard`) share the same bug and the same
guard, so both are covered here.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/skills.js"

# The guard the fix introduces inside the card click handler.
GUARD = re.compile(r"querySelector\(\s*['\"]\.skill-md-editor['\"]\s*\)\s*\)\s*return")


def _handler_body(text: str, anchor: str, call: str) -> str:
    """Return the card click-handler body: the slice from `anchor` (a string
    unique to the handler we care about) up to its collapse trigger `call`.
    `_expandSkillCard` is called from several places, so we must anchor on the
    handler itself rather than the first textual match of the call."""
    start = text.index(anchor)
    end = text.index(call, start)
    return text[start:end]


def test_user_skill_card_does_not_collapse_while_editing():
    text = SRC.read_text(encoding="utf-8")
    body = _handler_body(
        text, "// Click to expand/collapse", "_expandSkillCard(card, name)"
    )
    assert GUARD.search(body), (
        "user-skill card click handler must skip collapse while a "
        ".skill-md-editor is present (issue #4002)"
    )


def test_builtin_card_does_not_collapse_while_editing():
    text = SRC.read_text(encoding="utf-8")
    # The built-in capability card has a single handler ending in
    # _expandBuiltinCard; take the click handler that immediately precedes it.
    before = text[: text.index("_expandBuiltinCard(card, b.name)")]
    body = before[before.rindex("card.addEventListener('click'"):]
    assert GUARD.search(body), (
        "built-in capability card click handler must skip collapse while a "
        ".skill-md-editor is present (issue #4002)"
    )
