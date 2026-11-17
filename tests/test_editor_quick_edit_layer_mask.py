import re
from pathlib import Path

GALLERY = Path(__file__).resolve().parents[1] / "static" / "js" / "galleryEditor.js"
DOC = Path(__file__).resolve().parents[1] / "docs" / "QUICKEDIT_COVERAGE.md"


def _src():
    return GALLERY.read_text(encoding="utf-8")


def test_mask_block_routes_all_operations():
    src = _src()
    assert "// --- Layer ▸ Layer Mask (deterministic) ---" in src
    assert "if (/\\bmask\\b/.test(p)) {" in src
    # remove / apply / invert / feather / add
    assert "_runQuickControlOp('layers.removeMask', {});" in src
    assert "'Removed the layer mask.'" in src
    assert "_runQuickControlOp('layers.applyMask', {});" in src
    assert "'Applied the layer mask.'" in src
    assert "_runQuickControlOp('mask.invert', {});" in src
    assert "'Inverted the layer mask.'" in src
    assert "_runQuickControlOp('mask.feather', { radius: Math.min(100, _mRadius) });" in src
    assert "'Feathered the layer mask.'" in src


def test_mask_add_reveal_vs_hide():
    src = _src()
    assert "_mHide" in src
    assert "mask\\s*out|mask\\s*away|invert" in src
    assert "await _runQuickControlOp('layers.addMaskFromSelection', _mHide ? { invert: true } : {});" in src
    assert "'Added a layer mask (hiding the selection).'" in src
    assert "'Added a layer mask (revealing the selection).'" in src


def test_mask_feather_amount_regex_and_clamp():
    src = _src()
    assert "const _mAmt = p.match(/\\b(?:by\\s+)?(\\d{1,3})\\s*(?:px|pixels?)?\\b/);" in src
    assert "const _mRadius = _mAmt ? Math.max(0, Math.round(Number(_mAmt[1]))) : 8;" in src
    assert "Math.min(100, _mRadius)" in src


def test_mask_block_ordering_after_rasterize_before_layer_management():
    src = _src()
    i_filter = src.index("SPECIALTY_FILTERS")
    i_raster = src.index("// --- Rasterize (Layer ▸ Rasterize) ---")
    i_mask = src.index("// --- Layer ▸ Layer Mask (deterministic) ---")
    i_layermgmt = src.index("// --- Layer management commands ---")
    assert i_filter < i_raster < i_mask < i_layermgmt


def test_mask_control_ops_registered():
    src = _src()
    assert "'layers.addMaskFromSelection': {" in src
    assert "'layers.removeMask': {" in src
    assert "'layers.applyMask': {" in src
    assert "'mask.invert': {" in src
    assert "'mask.feather': {" in src
    # layers.list exposes maskCount so probes/agent can see masks
    assert "maskCount: (l.masks || []).length," in src


def test_coverage_doc_has_layer_mask_row():
    doc = DOC.read_text(encoding="utf-8")
    assert "Layer → Layer Mask" in doc
    assert "`layers.addMaskFromSelection`" in doc
    assert re.search(r"Layer → Layer Mask[^\n]*\|\s*OK\s*\|", doc)
