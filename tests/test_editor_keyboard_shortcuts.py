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


def test_enter_confirms_the_transform_even_from_a_transform_field():
    keyboard = (ROOT / "static/js/editor/keyboard-shortcuts.js").read_text(encoding="utf-8")
    # A form field would normally swallow Enter; an active transform must still
    # accept when Enter is pressed from one of its number inputs.
    assert "!(state.transformActive && e.key === 'Enter')) return;" in keyboard
    assert "if (e.key === 'Enter' && state.transformActive) {" in keyboard
    assert "confirmTransform();" in keyboard


def test_ctrl_zoom_shortcuts_drive_the_toolbar_and_override_the_browser():
    keyboard = (ROOT / "static/js/editor/keyboard-shortcuts.js").read_text(encoding="utf-8")
    # Ctrl/Cmd +/-/0 zoom the canvas (they call the toolbar zoom buttons and
    # preventDefault so they win over the browser's page zoom).
    assert "document.getElementById('ge-zoom-in')?.click();" in keyboard
    assert "document.getElementById('ge-zoom-out')?.click();" in keyboard
    assert "document.getElementById('ge-zoom-100')?.click();" in keyboard
    assert "e.code === 'NumpadAdd'" in keyboard
    assert "e.code === 'NumpadSubtract'" in keyboard


def test_ctrl_alt_l_opens_the_levels_adjustment():
    keyboard = (ROOT / "static/js/editor/keyboard-shortcuts.js").read_text(encoding="utf-8")
    # Photoshop-style chord: reuse the Image menu row so the dialog and the
    # adjustment-layer plumbing stay in one place.
    assert "e.altKey && e.code === 'KeyL'" in keyboard
    assert "document.querySelector('[data-image-action=\"adj-levels\"]')" in keyboard


def test_ctrl_alt_shift_e_stamps_the_visible_layers():
    keyboard = (ROOT / "static/js/editor/keyboard-shortcuts.js").read_text(encoding="utf-8")
    # Photoshop's Stamp Visible reuses the layer footer's "Flatten Copy", which
    # merges every visible layer into a new layer on top and keeps the originals.
    assert "e.altKey && e.shiftKey && e.code === 'KeyE'" in keyboard
    assert "document.getElementById('ge-flatten')" in keyboard
