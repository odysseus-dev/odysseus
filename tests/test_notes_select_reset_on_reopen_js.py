"""openPanel must reset _selectMode/_selectedIds so a reopened Notes panel isn't
stuck in bulk-select mode after a non-Esc close. The rebuilt pane renders the
Select button as "Select" and the bulk bar hidden, but _renderNotes reads
module-scope _selectMode — so a stale _selectMode leaves phantom checkboxes (with
stale _selectedIds) on reopen and disables drag-reorder until Select is toggled.

Sibling of issue #2919's _searchQuery reset. notes.js is a browser ES module with
a heavy import chain (can't node-import in isolation), so — per the repo's
DOM-coupled-guard convention — this asserts the reset is present in openPanel.
"""
import re
from pathlib import Path

SRC = Path("static/js/notes.js").read_text(encoding="utf-8")


def _open_panel_body():
    start = SRC.index("export function openPanel()")
    rest = SRC[start + len("export function openPanel()"):]
    m = re.search(r"\n(?:export\s+)?(?:async\s+)?function ", rest)
    return rest[: m.start()] if m else rest


def test_open_panel_resets_select_mode():
    body = _open_panel_body()
    # the bulk-select state must be cleared on open, beside the other resets
    assert "_selectMode = false" in body, body[:600]
    assert "_selectedIds.clear()" in body, body[:600]


def test_module_still_declares_select_state():
    assert "let _selectMode = false" in SRC
    assert "let _selectedIds = new Set()" in SRC
