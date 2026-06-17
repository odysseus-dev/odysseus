"""Issue #2919 — openPanel must reset _searchQuery so a reopened Notes panel
doesn't keep filtering by a stale query (the rebuilt search box renders empty).

notes.js is a browser ES module with a heavy import chain (can't node-import in
isolation), so — per the repo's DOM-coupled-guard convention — this asserts the
reset is present in openPanel, beside the existing _editingId reset.
"""
import re
from pathlib import Path

SRC = Path("static/js/notes.js").read_text(encoding="utf-8")


def _open_panel_body():
    start = SRC.index("export function openPanel()")
    rest = SRC[start + len("export function openPanel()"):]
    m = re.search(r"\n(?:export\s+)?(?:async\s+)?function ", rest)
    return rest[: m.start()] if m else rest


def test_open_panel_resets_search_query():
    body = _open_panel_body()
    assert "_searchQuery = ''" in body, body[:400]
    # reset must sit with the other open-time state resets, before render
    assert body.index("_searchQuery = ''") < body.index("_renderNotes") if "_renderNotes" in body else True


def test_module_still_declares_search_query():
    assert "let _searchQuery = ''" in SRC
