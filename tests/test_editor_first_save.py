from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_first_editor_save_is_immediate_but_existing_drafts_are_debounced():
    source = (ROOT / "static/js/galleryEditor.js").read_text()
    start = source.index("function _schedulePersist()")
    end = source.index("async function _buildDraftPayloadAsync", start)
    block = source[start:end]

    assert "const delay = state.draftId ? PERSIST_DEBOUNCE_MS : 0;" in block
    assert "setTimeout(() => { state.persistTimer = null; _persistDraft(); }, delay)" in block
