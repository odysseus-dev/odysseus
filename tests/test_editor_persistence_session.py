from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_persistence_snapshots_metadata_and_guards_late_callbacks():
    source = (ROOT / "static/js/galleryEditor.js").read_text()
    persist = source[source.index("async function _persistDraft"):source.index("async function _loadDraftById")]

    assert "const sessionToken = state.editorSessionToken;" in persist
    assert "const draftIdAtStart = state.draftId || null;" in persist
    assert "const draftNameAtStart = state.draftName || 'Untitled';" in persist
    assert "const isCurrentSession = () => state.editorOpen && state.editorSessionToken === sessionToken;" in persist
    assert "if (!isCurrentSession()) return;" in persist
    assert "state.draftId = out.id;" in persist


def test_new_editor_invalidates_previous_persistence_lane():
    state = (ROOT / "static/js/editor/state.js").read_text()
    editor = (ROOT / "static/js/galleryEditor.js").read_text()

    assert "editorSessionToken: 0" in state
    assert "state.editorSessionToken += 1;" in editor
    assert "state.persistInFlight = null;" in editor
