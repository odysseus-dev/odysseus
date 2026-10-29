import re
from pathlib import Path

GALLERY = Path(__file__).resolve().parents[1] / "static" / "js" / "galleryEditor.js"
DOC = Path(__file__).resolve().parents[1] / "docs" / "QUICKEDIT_COVERAGE.md"

BLOCK = "// --- Layer ▸ Combine (unite / subtract / intersect / exclude) ---"


def _src():
    return GALLERY.read_text(encoding="utf-8")


def _block():
    src = _src()
    start = src.index(BLOCK)
    return src[start:src.index("// --- Rasterize (Layer ▸ Rasterize) ---", start)]


def test_combine_block_routes_combine_selected_shapes():
    src = _src()
    assert BLOCK in src
    assert re.search(r"if \(/\\b\(\?:unite\|combine\|merge\|intersect\|exclude\|subtract\)\\b", src)
    assert "\\b(?:shapes?|rectangles?|ellipses?|circles?)\\b/.test(p)" in src
    assert "const _combineShapes = (state.layers || []).filter((l) => l && (l.kind === 'shape' || l.shape));" in src
    assert "_combineLayers(_combineShapes, _combineOp, { width: state.imgWidth, height: state.imgHeight, offsets: _cOffsets });" in src
    assert "`Combined the shapes (${_combineOp}).`" in src


def test_combine_guards_two_or_more_shapes():
    block = _block()
    assert "if (_combineShapes.length < 2) {" in block
    assert "'Add at least two shapes to combine.'" in block
    assert "state.selectedLayerIds = [_cBase.id];" in block
    assert "state.activeLayerId = _cBase.id;" in block


def test_combine_op_resolution():
    block = _block()
    assert "const _combineOp = /\\bintersect\\b/.test(p) ? 'intersect'" in block
    assert ": /\\bexclude\\b/.test(p) ? 'exclude'" in block
    assert ": (/\\bsubtract\\b/.test(p) || /\\bminus\\b/.test(p)) ? 'subtract'" in block
    assert ": 'unite';" in block


def test_combine_requires_shape_noun_and_ordering():
    src_block = _block()
    # the verb must be paired with a shape noun (keeps us clear of merge/flatten)
    assert "shapes?|rectangles?|ellipses?|circles?" in src_block
    src = _src()
    i_transform = src.index("// --- Edit ▸ Free Transform (deterministic) ---")
    i_combine = src.index(BLOCK)
    i_raster = src.index("// --- Rasterize (Layer ▸ Rasterize) ---")
    assert i_transform < i_combine < i_raster


def test_combine_infrastructure_present():
    src = _src()
    assert "combineLayers as _combineLayers" in src
    assert "function _combineSelectedShapes(op) {" in src
    assert "combineShapes: (op) => _combineSelectedShapes(op)" in src


def test_coverage_doc_has_layer_combine_row():
    doc = DOC.read_text(encoding="utf-8")
    assert "Layer → Combine" in doc
    assert "`_combineSelectedShapes`" in doc
    assert re.search(r"Layer → Combine[^\n]*\|\s*OK[^\n]*\|", doc)
