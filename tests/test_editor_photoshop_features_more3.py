"""Marker tests for the fourth batch of Photoshop-style editor commands.

These assert the wiring for Edit > Copy Merged, Layer > Merge Layers,
Filter > Other > Offset, Filter > Stylize > Glowing Edges and
View > New (horizontal/vertical) Guide. Copy Merged reuses the
flattened composite + internal clipboard, Merge Layers reuses the
`drawLayerStack` merge machinery, Offset/Glowing Edges reuse the
live-filter dialog (and `findEdges`), and New Guide reuses the
precision-guides overlay.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text()
WIRE = (ROOT / "static/js/editor/wire-topbar-menus.js").read_text()
EXTRA = (ROOT / "static/js/editor/wire-extra-menus.js").read_text()
VIEW = (ROOT / "static/js/editor/wire-view-menu.js").read_text()
MERGE = (ROOT / "static/js/editor/wire-merge-buttons.js").read_text()
GUIDES = (ROOT / "static/js/editor/precision-guides.js").read_text()
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()
SW = (ROOT / "static/sw.js").read_text()


def test_edit_copy_merged_menu_item_present():
    assert 'data-edit-action="copy-merged"' in TOPBAR


def test_layer_merge_layers_menu_item_present():
    assert 'data-layer-action="merge-layers"' in TOPBAR


def test_filter_offset_and_glowing_edges_menu_items_present():
    assert 'data-filter-action="other-offset"' in TOPBAR
    assert 'data-filter-action="stylize-glowing-edges"' in TOPBAR


def test_view_new_guide_menu_items_present():
    assert 'data-view-action="new-h-guide"' in TOPBAR
    assert 'data-view-action="new-v-guide"' in TOPBAR


def test_copy_merged_wire_branch_present():
    assert "action === 'copy-merged'" in EXTRA
    assert "action === 'copy-merged') window.geEditorControl?.invoke('edit.copyMerged', {});" in EXTRA


def test_merge_layers_wire_branch_present():
    assert "action === 'merge-layers'" in EXTRA
    assert "action === 'merge-layers') window.geEditorControl?.invoke('layers.mergeLayers', {});" in EXTRA


def test_filter_offset_glowing_edges_wire_branches_present():
    assert "action === 'other-offset'" in WIRE
    assert "action === 'stylize-glowing-edges'" in WIRE
    assert "else if (action === 'other-offset') applyOffset?.();" in WIRE
    assert "else if (action === 'stylize-glowing-edges') applyGlowingEdges?.();" in WIRE


def test_view_new_guide_wire_branches_present():
    assert "action === 'new-h-guide'" in VIEW
    assert "action === 'new-v-guide'" in VIEW
    assert "precisionGuides.addGuide?.('horizontal'" in VIEW
    assert "precisionGuides.addGuide?.('vertical'" in VIEW


def test_copy_merged_op_present():
    assert "'edit.copyMerged': {" in EDITOR
    assert "state.internalClipboard = flat;" in EDITOR
    assert "const flat = flatten();" in EDITOR


def test_merge_layers_op_present():
    assert "'layers.mergeLayers': {" in EDITOR
    assert "_mergeLayersByIds(ids, _renderLayerOutput)" in EDITOR


def test_merge_layers_reuses_layer_stack_machinery():
    assert "export function mergeLayersByIds(ids, renderLayer = null)" in MERGE
    assert "drawLayerStack(merged.getContext('2d'), state, targets" in MERGE
    assert "normalizeLayerClipping(state);" in MERGE


def test_offset_and_glowing_edges_ops_present():
    assert "function _applyOffset()" in EDITOR
    assert "function _applyGlowingEdges()" in EDITOR
    assert "function _offset(snap, v, dst) {" in EDITOR
    assert "function _glowingEdges(snap, v, dst) {" in EDITOR
    assert "_findEdges(snap, v, ectx);" in EDITOR
    assert "applyOffset: () => _applyOffset()," in EDITOR
    assert "applyGlowingEdges: () => _applyGlowingEdges()," in EDITOR


def test_new_guide_reuses_precision_guides():
    assert "addGuide(orientation, position) {" in GUIDES
    assert "saveState?.('Add guide');" in GUIDES


def test_sw_version_is_current():
    m = re.search(r"const CACHE_NAME = '([^']+)'", SW)
    assert m, "CACHE_NAME not found"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1)), m.group(1)
