"""Keyboard-shortcut audit for the image editor.

One test per audited key: the single-letter tool keys (V M L W C B E G T Z H)
plus the chords Ctrl+Z, Ctrl+Shift+Z, Ctrl+J, Ctrl+D, Ctrl+T and the brush-size
brackets. Each test pins the exact dispatch wiring so a regression in any key
is caught without launching a browser.
"""

import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL_SHORTCUTS_JS = (ROOT / "static/js/editor/tool-shortcuts.js").read_text(encoding="utf-8")
TOOLBAR_JS = (ROOT / "static/js/editor/build/toolbar.js").read_text(encoding="utf-8")
KEYBOARD_JS = (ROOT / "static/js/editor/keyboard-shortcuts.js").read_text(encoding="utf-8")
EDITOR_JS = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")


def _tool_shortcuts():
    body = TOOL_SHORTCUTS_JS[TOOL_SHORTCUTS_JS.index("{"): TOOL_SHORTCUTS_JS.index("}")]
    return dict(re.findall(r"(\w+):\s*'([^']+)'", body))


AUDITED_TOOL_KEYS = {
    "move": "V",
    "marquee": "M",
    "lasso": "L",
    "wand": "W",
    "crop": "C",
    "brush": "B",
    "eraser": "E",
    "gradient": "G",
    "text": "T",
    "zoom": "Z",
    "hand": "H",
}


@pytest.mark.parametrize("tool,key", sorted(AUDITED_TOOL_KEYS.items()))
def test_tool_key_selects_its_tool(tool, key):
    # The key is registered in the shared shortcut map …
    assert _tool_shortcuts()[tool] == key
    # … the tool exists in the palette (so a `[data-tool]` button is rendered) …
    assert ("\n  " + tool + ": { label: '") in TOOLBAR_JS
    # … and the keyboard handler focuses the palette by key.
    assert "const toolId = toolKeyMap[e.key.toLowerCase()];" in KEYBOARD_JS
    assert 'const flyoutItem = toolbar.querySelector(`[data-tool-option="${toolId}"]`);' in KEYBOARD_JS
    assert "const slotEl = flyoutItem ? flyoutItem.closest('.ge-tool-slot') : null;" in KEYBOARD_JS
    assert "toolBtn = toolBtn || toolbar.querySelector(`[data-tool=\"${toolId}\"]`) || flyoutItem;" in KEYBOARD_JS
    assert "if (toolBtn) toolBtn.click();" in KEYBOARD_JS


def test_tool_shortcut_keys_are_unique_and_single_letters():
    mapping = _tool_shortcuts()
    keys = list(mapping.values())
    assert len(keys) == len(set(keys)), "two tools share a shortcut letter"
    assert all(len(k) == 1 and k.isalpha() for k in keys)


def test_zoom_tool_z_is_wired_to_a_real_canvas_behaviour():
    # Z must not be a dead key: it selects the Zoom tool AND the tool must
    # actually zoom on canvas click (Alt-click zooms out), like Photoshop.
    assert "zoom: 'Z'," in TOOL_SHORTCUTS_JS
    assert "function _zoomToolClick(e)" in EDITOR_JS
    assert "if (state.tool === 'zoom') return _zoomToolClick(e);" in EDITOR_JS
    assert "const zoomOut = !!(e && (e.altKey || e.metaKey));" in EDITOR_JS
    assert "state.mainCanvas.style.cursor" in EDITOR_JS
    assert "else if (state.tool === 'zoom') cursor = 'zoom-in';" in EDITOR_JS


def test_ctrl_z_undo_and_ctrl_shift_z_redo():
    assert "if (e.key.toLowerCase() === 'z') {" in KEYBOARD_JS
    assert "if (e.shiftKey) redo(); else undo();" in KEYBOARD_JS
    # Undo/redo must override the browser's native undo inside the editor.
    assert "e.preventDefault(); e.stopPropagation();" in KEYBOARD_JS


def test_ctrl_j_duplicates_selection_pixels_or_the_active_layer():
    assert "if (!e.altKey && e.code === 'KeyJ') {" in KEYBOARD_JS
    assert "if (state.wandMask) wandCopyToNewLayer();" in KEYBOARD_JS
    assert "else if (state.lassoPoints.length >= 3) lassoCopyToLayer();" in KEYBOARD_JS
    assert "else duplicateActiveLayer?.();" in KEYBOARD_JS
    # Ctrl+Alt+J stays the "new empty layer" variant.
    assert "if (e.altKey && e.code === 'KeyJ') {" in KEYBOARD_JS
    assert "addEmptyLayer();" in KEYBOARD_JS


def test_ctrl_d_deselects():
    assert "if (!e.altKey && e.key.toLowerCase() === 'd') {" in KEYBOARD_JS
    assert "deselectSelection?.();" in KEYBOARD_JS


def test_ctrl_t_starts_free_transform_while_shift_and_alt_stay_resize_and_legacy():
    # Plain Ctrl/Cmd+T starts a free transform (Photoshop parity). The branch
    # is keyed on !shiftKey, so Ctrl+Alt+T (the legacy alias) also lands here.
    assert "if (!e.shiftKey && e.code === 'KeyT') { e.preventDefault(); e.stopPropagation(); startTransform(); return; }" in KEYBOARD_JS
    # Ctrl+Shift+T keeps the canvas-resize prompt.
    assert "if (e.shiftKey && e.key === 'T') { e.preventDefault(); e.stopPropagation(); resizeCustomPrompt(); return; }" in KEYBOARD_JS


def test_bracket_keys_scale_the_brush_proportionally():
    assert "if (e.key === '[' || e.key === ']') {" in KEYBOARD_JS
    assert "const factor = e.key === '[' ? 0.9 : 1.1;" in KEYBOARD_JS
    assert "state.brushSize = Math.max(1, Math.min(800, Math.round(state.brushSize * factor)));" in KEYBOARD_JS
    assert "brushSizeSync(null);" in KEYBOARD_JS


def test_shortcuts_are_gated_to_an_open_editor_and_ignore_typing():
    # The whole handler bails when the editor is closed or the event is consumed.
    assert "if (!state.editorOpen || e.defaultPrevented || e.isComposing) return;" in KEYBOARD_JS
    # Tool keys don't hijack text fields.
    assert "if (isTypingTarget(e.target) && !isNonTextInput(e.target)) return;" in KEYBOARD_JS
