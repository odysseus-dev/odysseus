from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_delete_shortcut_delegates_to_guarded_layer_deletion_after_selection_paths():
    keyboard = (ROOT / "static/js/editor/keyboard-shortcuts.js").read_text(encoding="utf-8")
    panel = (ROOT / "static/js/editor/layer-panel.js").read_text(encoding="utf-8")

    assert "deleteSelectedLayers" in keyboard
    assert "duplicateActiveLayer" in keyboard
    assert "state.wandMask" in keyboard
    assert "state.lassoPoints.length >= 3" in keyboard
    assert "state.transformActive || state.cropping || state.cropMoving" in keyboard
    assert "activeGroupMask" in keyboard
    assert "deleteSelectedLayers: () => deleteLayers(selectedLayers(state))" in panel
    assert "duplicateActiveLayer: () =>" in panel
