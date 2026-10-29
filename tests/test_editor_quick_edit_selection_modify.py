from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"


def _selection_block(src):
    # The Photoshop Select ▸ Modify branches live inside the selection command
    # block, between the invert command and the subject selection.
    start = src.index("// Photoshop Select ▸ Modify: Feather / Expand / Contract.")
    end = src.index("if (/\\bsubject\\b|\\bperson\\b|\\bpeople\\b/.test(p)) {", start)
    return src[start:end]


def test_feather_expand_contract_are_deterministic_local_ops():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert "await _runQuickControlOp('selection.feather', { radius });" in block
    assert "await _runQuickControlOp('selection.expand', { amount });" in block
    assert "await _runQuickControlOp('selection.contract', { amount });" in block


def test_modify_keywords_and_parsed_amount():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert r"/\bfeather\b|\bsoften\b/" in block
    assert r"/\bexpand\b|\bgrow\b|\benlarge\b|\bbigger\b/" in block
    assert r"/\bcontract\b|\bshrink\b|\btighten\b|\breduce\b/" in block
    # A parsed "by N px" amount with Photoshop-style defaults (8 / 4).
    assert r"\b(?:by\s+)?(\d{1,3})\s*(?:px|pixels?)?\b" in block
    assert "_selAmount(8)" in block
    assert "_selAmount(4)" in block
    # Clamped to the op contracts (feather 0..100, expand/contract 0..64).
    assert "Math.min(100, _selAmount(8))" in block
    assert "Math.min(64, _selAmount(4))" in block


def test_modify_ops_are_registered():
    src = EDITOR.read_text(encoding="utf-8")
    assert "'selection.feather': { description: 'Feather" in src
    assert "'selection.expand': { description: 'Expand" in src
    assert "'selection.contract': { description: 'Contract" in src


def test_grow_mask_canvas_contracts_by_erosion():
    # Contract must erode (off-canvas neighbours count as unselected) rather than
    # returning the complement of the dilated mask, which shrank to nothing.
    src = EDITOR.read_text(encoding="utf-8")
    start = src.index("const _growMaskCanvas = (canvas, radius, grow) => {")
    body = src[start:src.index("const _differenceMaskCanvas", start)]
    assert "(xx >= 0 && xx < w) ? on[y * w + xx] : 0" in body
    assert "(yy >= 0 && yy < h) ? horiz[yy * w + x] : 0" in body
    assert "id.data[p * 4 + 3] = a * 255;" in body
    assert "1 - a" not in body
