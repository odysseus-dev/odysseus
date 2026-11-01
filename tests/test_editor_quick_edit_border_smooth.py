from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"
SELECTION_MASK = ROOT / "static/js/editor/selection-mask.js"


def _selection_block(src):
    start = src.index("// Photoshop Select ▸ Modify: Feather / Expand / Contract.")
    end = src.index("if (/\\bsubject\\b|\\bperson\\b|\\bpeople\\b/.test(p)) {", start)
    return src[start:end]


def test_border_branch_is_deterministic_local_op():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert r"if (/\bborder\b/.test(p)) {" in block
    assert "const base = _selectionMaskAsDocument({ materializeLasso: true });" in block
    assert "_saveState('Border selection');" in block
    assert "_refineSelectionMask(base, { border: width })" in block
    assert "Added a ${width}px border to the selection." in block
    assert "There is no active selection to border." in block


def test_smooth_branch_is_deterministic_local_op():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert r"if (/\bsmooth\b/.test(p)) {" in block
    assert "_saveState('Smooth selection');" in block
    assert "_refineSelectionMask(base, { smooth: amount })" in block
    assert "Smoothed the selection by ${amount}px." in block
    assert "There is no active selection to smooth." in block


def test_border_smooth_parse_amount_and_ordering():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    # Reuse the shared "by N px" parser with Photoshop-style defaults (5 / 4).
    assert "_selAmount(5)" in block
    assert "_selAmount(4)" in block
    assert "Math.min(100, Math.max(1, _selAmount(5)))" in block
    assert "Math.min(50, Math.max(1, _selAmount(4)))" in block
    # Border/Smooth come after contract and before the Edit-menu cut branch.
    assert block.index(r"/\bcontract\b") < block.index(r"if (/\bborder\b/.test(p)) {")
    assert block.index(r"if (/\bborder\b/.test(p)) {") < block.index(r"if (/\bsmooth\b/.test(p)) {")
    assert block.index(r"if (/\bsmooth\b/.test(p)) {") < block.index(r"if (/\bcut\b/.test(p)) {")


def test_refine_selection_mask_supports_border_and_smooth():
    src = SELECTION_MASK.read_text(encoding="utf-8")
    assert "export function refineSelectionMask(mask, options = {}) {" in src
    assert "const smooth = Math.max(0, Math.min(100, Math.round(Number(options.smooth) || 0)));" in src
    assert "const border = Math.max(0, Math.min(200, Math.round(Number(options.border) || 0)));" in src
    assert "if (smooth) result = thresholdMask(blurMask(result, smooth), 128);" in src
    assert "if (border) {" in src


def test_refine_selection_mask_is_imported():
    src = EDITOR.read_text(encoding="utf-8")
    assert "refineSelectionMask as _refineSelectionMask," in src
