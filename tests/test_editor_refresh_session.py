from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_blank_project_records_refresh_metadata_after_size_is_selected():
    source = (ROOT / "static/js/galleryEditor.js").read_text()
    start = source.index("const _finishBlank = (w, h) => {")
    end = source.index("    };", start)
    block = source[start:end]

    assert "state.activeLayerId = editLayer.id;" in block
    assert "_writeActiveEditorSession();" in block
