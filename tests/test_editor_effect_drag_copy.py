import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PANEL = (ROOT / "static/js/editor/layer-panel.js").read_text(encoding="utf-8")
SORT = (ROOT / "static/js/dragSort.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")


def test_effect_rows_can_be_dragged_onto_layers_to_copy_them():
    # Photoshop-style: drag an effect sub-row onto another layer/group and it
    # is copied there (the source keeps its effect).
    assert "function cloneEffectForCopy(effect) {" in PANEL
    assert "function wireEffectDrag(sub, owner, effect) {" in PANEL
    assert "wireEffectDrag(sub, group, effect);" in PANEL
    assert "wireEffectDrag(sub, layer, effect);" in PANEL
    assert "'.ge-layer-item[data-layer-id], .ge-layer-group-row[data-group-id]'" in PANEL
    assert "layer.effects.push(cloneEffectForCopy(effect));" in PANEL
    assert "saveState(`Copy ${effectLabel(effect.type)}`);" in PANEL
    assert "copied to ${layer.name}" in PANEL


def test_effect_copy_gets_fresh_ids_and_its_own_mask_canvas():
    clone = PANEL[PANEL.index("function cloneEffectForCopy(effect) {"):]
    clone = clone[:clone.index("\n  }")]
    assert "id: `effect-${Math.random().toString(36).slice(2, 9)}`," in clone
    assert "id: `effect-mask-${Math.random().toString(36).slice(2, 9)}`" in clone
    assert "const canvas = document.createElement('canvas');" in clone
    assert "canvas.getContext('2d').drawImage(effect.mask.canvas, 0, 0);" in clone
    assert "copy.mask.canvas = canvas;" in clone
    assert "copy.mask.ctx = canvas.getContext('2d');" in clone


def test_effect_drag_does_not_start_the_layer_reorder():
    enable = PANEL[PANEL.index("dragSortModule.enable('ge-layers-list'"):]
    enable = enable[:enable.index("onReorder:")]
    assert ".ge-adj-sub-item" in enable
    # A drop re-renders the list mid-drag, so the snap-back must bail out when
    # the placeholder/dragged row were already detached.
    assert "if (!placeholder.parentNode || !draggedEl.isConnected) {" in SORT
    assert "placeholder.remove();\n        return;" in SORT


def test_effect_drag_has_ghost_and_drop_target_styles():
    assert ".ge-effect-drag-ghost {" in CSS
    ghost = CSS[CSS.index(".ge-effect-drag-ghost {"):]
    ghost = ghost[:ghost.index("}")]
    assert "position: fixed;" in ghost
    assert "pointer-events: none;" in ghost
    assert "z-index: 10030;" in ghost
    assert ".ge-layer-item.is-effect-drop-target," in CSS
    assert ".ge-layer-group-row.is-effect-drop-target {" in CSS
    assert ".ge-layer-item.is-effect-dragging {" in CSS
