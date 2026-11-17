from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"
STATE = ROOT / "static/js/editor/state.js"


def _selection_block(src):
    start = src.index("// --- Selection commands (deterministic, no model call) ---")
    return src[start:src.index("// --- Layer align", start) if "// --- Layer align" in src[start:] else start + 4000]


def test_reselect_word_is_admitted_into_the_selection_block():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    # "reselect" is a single token, so \bselect\b never matched it before; the
    # outer guard must admit it explicitly.
    assert "if (/\\bselect\\b|\\bselection\\b|\\bdeselect\\b|\\binvert\\b|\\binverse\\b|\\breselect\\b/.test(p)) {" in block


def test_reselect_branch_calls_the_helper_and_reports_status():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert "if (/\\breselect\\b|\\bbring\\s+back\\s+(?:the\\s+)?selection\\b|\\brestore\\s+(?:the\\s+)?selection\\b/.test(p)) {" in block
    assert "if (_reselectSelection()) _setAiCommandStatus('Reselected the last selection.', 'done');" in block
    assert "else _setAiCommandStatus('There is nothing to reselect yet.', '');" in block
    assert "return;" in block


def test_reselect_branch_precedes_select_all():
    src = EDITOR.read_text(encoding="utf-8")
    reselect = src.index("if (/\\breselect\\b|\\bbring\\s+back\\s+(?:the\\s+)?selection\\b")
    select_all = src.index("if (/\\bselect\\s+(?:all|everything)\\b", reselect)
    assert reselect < select_all


def test_reselect_helper_and_last_selection_state_exist():
    src = EDITOR.read_text(encoding="utf-8")
    assert "function _reselectSelection() {" in src
    assert "if (!state.lastSelection?.canvas) return false;" in src
    assert "return _activateSelectionCanvas(state.lastSelection.canvas, 'reselect');" in src
    # A dismissed selection is remembered; Reselect brings it back.
    assert "state.lastSelection = { canvas: _cloneSelectionCanvas(selection) };" in src
    state_src = STATE.read_text(encoding="utf-8")
    assert "lastSelection: null," in state_src
