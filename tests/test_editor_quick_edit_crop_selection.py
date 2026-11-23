from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"


def test_selection_bounds_helper_returns_document_scaled_bbox():
    src = EDITOR.read_text(encoding="utf-8")
    start = src.index("function _selectionBounds(mask, docW = 0, docH = 0) {")
    block = src[start:src.index("function _activateSelectionCanvas", start)]
    assert "data[(row + x) * 4 + 3] > 8" in block
    assert "const sx = docW > 0 ? docW / mask.width : 1;" in block
    assert "const sy = docH > 0 ? docH / mask.height : 1;" in block
    assert "width: Math.max(1, Math.round((maxX - minX + 1) * sx))," in block


def test_crop_to_selection_is_a_deterministic_local_op():
    src = EDITOR.read_text(encoding="utf-8")
    assert "if (/\\bcrop\\b/.test(p) && /\\bselection\\b|\\bselected\\b/.test(p) && state.wandMask) {" in src
    assert "const b = _selectionBounds(state.wandMask, state.imgWidth, state.imgHeight);" in src
    assert "await _runQuickControlOp('document.crop', { x: b.x, y: b.y, width: b.width, height: b.height });" in src


def test_crop_to_selection_precedes_the_aspect_ratio_crop():
    src = EDITOR.read_text(encoding="utf-8")
    sel = src.index("if (/\\bcrop\\b/.test(p) && /\\bselection\\b|\\bselected\\b/.test(p) && state.wandMask) {")
    ratio = src.index("const cropRect = (/\\bextend\\b|\\bexpand\\b/.test(p)) ? null : _formatCropRect(prompt, state.imgWidth, state.imgHeight);")
    assert sel < ratio


def test_border_trim_fast_path_defers_to_the_selection_crop():
    src = EDITOR.read_text(encoding="utf-8")
    assert "if (tryLocal && /\\b(?:crop|trim)\\b/i.test(cropText) && !hasRatio && !/\\bselection\\b|\\bselected\\b/i.test(cropText)) {" in src
