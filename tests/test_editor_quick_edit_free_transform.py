import re
from pathlib import Path

GALLERY = Path(__file__).resolve().parents[1] / "static" / "js" / "galleryEditor.js"
DOC = Path(__file__).resolve().parents[1] / "docs" / "QUICKEDIT_COVERAGE.md"


def _src():
    return GALLERY.read_text(encoding="utf-8")


def test_free_transform_block_routes_transform_start():
    src = _src()
    assert "// --- Edit ▸ Free Transform (deterministic) ---" in src
    assert "if (/\\bfree[-\\s]*transform\\b/.test(p)" in src
    assert "|| (/\\btransform\\b/.test(p) && /\\b(?:selection|selected|marquee)\\b/.test(p))) {" in src
    assert "await _runQuickControlOp('transform.start', {});" in src
    assert "'Started a free transform. Drag the handles, then press Enter.'" in src


def test_free_transform_selection_branch():
    src = _src()
    assert "state.wandMask && typeof _startSelectionTransform === 'function'" in src
    assert "_startSelectionTransform();" in src
    assert "'Transforming the selection. Drag the handles, then press Enter.'" in src


def test_free_transform_requires_explicit_phrase():
    src = _src()
    block = src[src.index("// --- Edit ▸ Free Transform (deterministic) ---"):]
    block = block[:block.index("// --- Rasterize")]
    # free-transform phrase OR transform + selection word; not a bare "transform"
    assert "free[-\\s]*transform" in block
    assert "selection|selected|marquee" in block


def test_free_transform_ordering_before_rasterize():
    src = _src()
    i_rotate = src.index("// \"rotate/flip the whole image/canvas/photo\"")
    i_transform = src.index("// --- Edit ▸ Free Transform (deterministic) ---")
    i_raster = src.index("// --- Rasterize (Layer ▸ Rasterize) ---")
    assert i_rotate < i_transform < i_raster


def test_transform_control_ops_registered():
    src = _src()
    assert "'transform.start': { description: 'Begin a transform.'" in src
    assert "'transform.apply': { description: 'Commit the active transform.'" in src
    assert "'transform.cancel': { description: 'Cancel the active transform.'" in src
    # _startSelectionTransform is the selection-content transform (from the session)
    assert "_startSelectionTransform" in src


def test_coverage_doc_has_free_transform_row():
    doc = DOC.read_text(encoding="utf-8")
    assert "Edit → Free Transform" in doc
    assert "`transform.start`" in doc
    assert re.search(r"Edit → Free Transform[^\n]*\|\s*OK\s*\|", doc)
