"""Regression for issue #1602 — after closing an AI-written document, its "Open"
button in the Documents library is grayed out, so the user can't reopen it.

Root cause: closing/detaching a document nulls its session_id (the detach
behaviour from #1238), and both Open controls in static/js/documentLibrary.js
(the card's expanded Open button AND the card dropdown's Open item) gated on
`doc.session_id` — wiring `libraryOpenInSession` (which early-returns when there's
no session) and DISABLING the control otherwise. But the module already has
`libraryOpenDocument`, which explicitly handles the orphaned case ("just open in
editor without switching session"). The fix routes the no-session path there
instead of disabling.

documentLibrary.js pulls in browser-only modules so it can't run under node; this
guards the wiring at the source level (red→green via git-stash).
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/documentLibrary.js"


def _src() -> str:
    return SRC.read_text(encoding="utf-8")


def test_orphaned_doc_open_controls_are_not_disabled():
    text = _src()
    # Neither Open control may hard-disable itself for a session-less doc anymore.
    assert "openItem.disabled = true" not in text, "dropdown Open must not be disabled for orphaned docs (#1602)"
    assert "openBtn.disabled = true" not in text, "card Open button must not be disabled for orphaned docs (#1602)"
    # The old 'not linked to a session' dead-end titles are gone.
    assert "not linked to a session" not in text.lower()


def test_orphaned_doc_open_routes_to_editor_load():
    """Both Open controls' no-session branch must call libraryOpenDocument, the
    function that opens an orphaned doc directly in the editor by id."""
    text = _src()
    # definition + two wirings (dropdown item + card button)
    assert text.count("libraryOpenDocument(doc)") >= 3, \
        "both Open controls must route the no-session case to libraryOpenDocument"
    # libraryOpenDocument genuinely handles the orphaned case.
    body = text[text.index("async function libraryOpenDocument(doc)"):]
    body = body[: body.index("async function libraryOpenInSession")]
    assert "if (!doc.session_id)" in body and "_loadDocument(doc.id)" in body, \
        "libraryOpenDocument must open a session-less doc by id"
