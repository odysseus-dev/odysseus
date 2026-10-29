"""Marker tests for the third batch of Photoshop-style editor commands.

These assert the wiring for Image > Adjustments > Equalize, Edit > Stroke,
Layer > Arrange (Bring to Front / Bring Forward / Send Backward / Send to
Back) and Select > Grow / Select > Similar. Equalize adds a small
deterministic histogram pass; the rest reuse existing ops / helpers
(`layers.reorder`, the retained stroke effect, `_growMaskCanvas` and the
colour-range selection machinery).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text()
WIRE = (ROOT / "static/js/editor/wire-topbar-menus.js").read_text()
EXTRA = (ROOT / "static/js/editor/wire-extra-menus.js").read_text()
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()


def test_image_equalize_menu_item_present():
    assert 'data-image-action="equalize"' in TOPBAR


def test_edit_stroke_menu_item_present():
    assert 'data-edit-action="stroke"' in TOPBAR


def test_layer_arrange_menu_items_present():
    for action in (
        'data-layer-action="arrange-front"',
        'data-layer-action="arrange-forward"',
        'data-layer-action="arrange-backward"',
        'data-layer-action="arrange-back"',
    ):
        assert action in TOPBAR, action


def test_selection_grow_similar_menu_items_present():
    assert 'data-selection-action="grow"' in TOPBAR
    assert 'data-selection-action="similar"' in TOPBAR


def test_image_equalize_wire_branch_present():
    assert "action === 'equalize'" in WIRE
    assert "else if (action === 'equalize') window.geEditorControl?.invoke?.('adjustments.equalize', {});" in WIRE


def test_selection_grow_similar_wire_branches_present():
    assert "action === 'grow'" in WIRE
    assert "action === 'similar'" in WIRE
    assert "else if (action === 'grow') handled = !!window.geEditorControl?.invoke?.('selection.grow', { amount: 1 });" in WIRE
    assert "else if (action === 'similar') handled = !!window.geEditorControl?.invoke?.('selection.similar', {});" in WIRE


def test_edit_stroke_wire_branch_present():
    assert "action === 'stroke'" in EXTRA
    assert "else if (action === 'stroke') document.querySelector('[data-filter-action=\"effect-stroke\"]')?.click();" in EXTRA


def test_layer_arrange_wire_branches_present():
    assert "action === 'arrange-front'" in EXTRA
    assert "action === 'arrange-forward'" in EXTRA
    assert "action === 'arrange-backward'" in EXTRA
    assert "action === 'arrange-back'" in EXTRA
    assert "action === 'arrange-front') window.geEditorControl?.invoke('layers.reorder', { direction: 'top' })" in EXTRA
    assert "action === 'arrange-forward') window.geEditorControl?.invoke('layers.reorder', { direction: 'up' })" in EXTRA
    assert "action === 'arrange-backward') window.geEditorControl?.invoke('layers.reorder', { direction: 'down' })" in EXTRA
    assert "action === 'arrange-back') window.geEditorControl?.invoke('layers.reorder', { direction: 'bottom' })" in EXTRA


def test_selection_grow_similar_ops_present():
    assert "'selection.grow': {" in EDITOR
    assert "'selection.similar': {" in EDITOR
    assert "_growMaskCanvas(state.wandMask, amt, true)" in EDITOR
    assert "_colorRangeMask(src.data, src.w, src.h, target, fuzz)" in EDITOR


def test_equalize_op_present():
    assert "'adjustments.equalize': {" in EDITOR
    assert "_writeLayerData(layer, out);" in EDITOR


def test_arrange_reuses_existing_reorder_op():
    assert "'layers.reorder': { description:" in EDITOR
    assert "direction:up|down|top|bottom" in EDITOR or "direction: up|down|top|bottom" in EDITOR


def test_stroke_reuses_retained_effect_machinery():
    assert 'data-filter-action="effect-stroke"' in TOPBAR
    assert "addRetainedEffect?.('stroke')" in WIRE
