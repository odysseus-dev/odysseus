from pathlib import Path

SRC = Path("static/js/notes.js").read_text(encoding="utf-8")

def _open_note_body():
    start = SRC.index("async function openNote(noteId)")
    rest = SRC[start + len("async function openNote(noteId)"):]
    end = rest.index("\nconst notesModule =")

    return rest[:end]

def test_open_note_refreshes_open_panel_without_closing_it():
    body = _open_note_body()
    assert "closePanel()" not in body
    assert "await _fetchNotes()" in body
    assert "_renderNotes()" in body

def test_open_note_clears_visibility_filters():
    body = _open_note_body()
    assert "_searchQuery = ''" in body
    assert "_activeLabel = null" in body
    assert "_activeFilter = null" in body
