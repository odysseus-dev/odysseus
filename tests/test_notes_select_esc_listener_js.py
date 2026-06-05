"""Issue #2791 — the Notes panel's capture-phase "Esc cancels select mode"
keydown listener must be tracked and removed on close, not leaked anonymously on
every open/close cycle.

notes.js is a browser ES module with a heavy import chain (can't be node-imported
in isolation), so — per the repo's convention for DOM-coupled guards (cf. the
document.js diff-discard and memory.js filter-guard tests) — this asserts the
tracked-handler pattern in source.
"""
from pathlib import Path

SRC = Path("static/js/notes.js").read_text(encoding="utf-8")


def test_select_esc_listener_is_tracked_not_anonymous():
    assert "let _notesSelectEscHandler = null;" in SRC
    # added via the tracked module-level var in capture phase
    assert "document.addEventListener('keydown', _notesSelectEscHandler, true);" in SRC


def test_select_esc_listener_removed_with_matching_capture_flag():
    # remove-before-add in openPanel + removal in both close paths => >= 3,
    # each with the `true` capture flag (a removal without it would not match).
    removals = SRC.count("document.removeEventListener('keydown', _notesSelectEscHandler, true);")
    assert removals >= 3, removals


def test_old_anonymous_capture_listener_is_gone():
    # the leak was an inline anonymous capture listener; it must no longer exist.
    assert "addEventListener('keydown', (e) => {\n    if (e.key === 'Escape' && _selectMode)" not in SRC
