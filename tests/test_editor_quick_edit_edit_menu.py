from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"


def _selection_block(src):
    start = src.index("// --- Selection commands (deterministic, no model call) ---")
    end = src.index("if (/\\bsubject\\b|\\bperson\\b|\\bpeople\\b/.test(p)) {", start)
    return src[start:end]


def test_cut_and_delete_selection_are_deterministic_local_ops():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert "if (/\\bcut\\b/.test(p)) {" in block
    assert "await _runQuickControlOp('selection.copy', { cut: true });" in block
    assert "if (/\\bdelete\\b/.test(p)) {" in block
    assert "await _runQuickControlOp('selection.delete', {});" in block


def test_fill_selection_uses_the_marquee_not_a_centre_flood():
    src = EDITOR.read_text(encoding="utf-8")
    fill_start = src.index("if (/\\bfill\\b|\\bflood\\s*fill\\b|\\bpaint\\s*bucket\\b/.test(p) && (fillWith || _fillFgBg)) {")
    fill_block = src[fill_start:src.index("// --- Selection commands", fill_start)]
    sel_branch = fill_block.index("if (/\\bselection\\b|\\bselected\\b/.test(p) && state.wandMask) {")
    sky_branch = fill_block.index("if (/\\bsky\\b/.test(p)) {")
    assert sel_branch < sky_branch
    assert "await _runQuickControlOp('selection.fill', { color });" in fill_block
    assert "await _runQuickControlOp('selection.sky', {});" in fill_block


def test_cut_and_delete_come_after_contract_and_before_subject():
    src = EDITOR.read_text(encoding="utf-8")
    contract = src.index("if (/\\bcontract\\b|\\bshrink\\b|\\btighten\\b|\\breduce\\b/.test(p)) {")
    cut = src.index("if (/\\bcut\\b/.test(p)) {")
    delete = src.index("if (/\\bdelete\\b/.test(p)) {")
    subject = src.index("if (/\\bsubject\\b|\\bperson\\b|\\bpeople\\b/.test(p)) {", delete)
    assert contract < cut < delete < subject
