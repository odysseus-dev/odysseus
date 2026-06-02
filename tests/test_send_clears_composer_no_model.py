"""Regression guard for issue #1475 — pressing Enter with no model selected left
the typed message in the composer.

The send flow clears the composer (`messageInput.value = ''`) only after it gets
past the no-session guard. With no model/session, that guard shows a "No chat
session active" guidance bubble and returns early — before the clear — so the
text stayed in the box. The early-return paths now also clear the composer.

chat.js pulls in browser globals so it can't run under node; guard at the source.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/chat.js"


def test_no_session_bail_clears_the_composer():
    text = SRC.read_text(encoding="utf-8")
    # A shared composer-clear helper exists...
    assert re.search(r"function _clearComposer\(\)", text)
    # ...and the "No chat session active" guidance path clears the composer
    # before returning (so the message isn't left in the box).
    block = text[text.index("No chat session active"):]
    # within the next ~600 chars (the two bail branches) the composer is cleared
    assert "_clearComposer()" in block[:800], "no-session bail must clear the composer (#1475)"
