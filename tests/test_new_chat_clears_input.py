"""Regression guard for issue #1343 — clicking "New chat" left the previous
session's draft text in the composer.

The direct model-picker path (sessions.js:createDirectChat) already cleared the
input, but the brand/welcome New-Chat navigation path did not. The shared entry
point for that state is chatRenderer.js:showWelcomeScreen(), which now clears the
`#message` composer. Switching between existing sessions loads them directly and
does not call showWelcomeScreen, so real drafts aren't erased.

chatRenderer.js pulls in browser globals, so it can't be imported under node;
this guards the fix at the source level so it can't be silently dropped.
"""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "static/js/compare").parent / "chatRenderer.js"


def _show_welcome_body() -> str:
    text = SRC.read_text(encoding="utf-8")
    start = text.index("export function showWelcomeScreen()")
    # Body runs until the next top-level `export function` / `function ` decl.
    rest = text[start + len("export function showWelcomeScreen()"):]
    m = re.search(r"\nexport function |\nfunction ", rest)
    return rest[: m.start()] if m else rest


def test_new_chat_welcome_clears_the_composer():
    body = _show_welcome_body()
    # Clears the draft value...
    assert re.search(r"getElementById\(['\"]message['\"]\)", body)
    assert re.search(r"\.value\s*=\s*['\"]['\"]", body), "must reset #message value"
    # ...and notifies listeners (send button icon / autosize) of the change.
    assert "new Event('input'" in body or 'new Event("input"' in body
