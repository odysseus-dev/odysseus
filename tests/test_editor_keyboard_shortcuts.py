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


def test_deleting_the_last_layer_is_allowed():
    panel = (ROOT / "static/js/editor/layer-panel.js").read_text(encoding="utf-8")
    # Every layer (including the final one) can be deleted; the document is then
    # an empty canvas rather than a blocked action.
    assert "Keep at least one layer" not in panel
    assert "if (fallback) selectOnlyLayer(state, fallback.id);" in panel
    assert "else { state.activeLayerId = null; state.selectedLayerIds = []; state.activeGroupId = null; }" in panel


def test_ctrl_zoom_shortcuts_drive_the_toolbar_and_override_the_browser():
    keyboard = (ROOT / "static/js/editor/keyboard-shortcuts.js").read_text(encoding="utf-8")
    # Ctrl/Cmd +/-/0 zoom the canvas (they call the toolbar zoom buttons and
    # preventDefault so they win over the browser's page zoom).
    assert "document.getElementById('ge-zoom-in')?.click();" in keyboard
    assert "document.getElementById('ge-zoom-out')?.click();" in keyboard
    assert "document.getElementById('ge-zoom-100')?.click();" in keyboard
    assert "e.code === 'NumpadAdd'" in keyboard
    assert "e.code === 'NumpadSubtract'" in keyboard
