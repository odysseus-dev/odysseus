from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"
STATE = ROOT / "static/js/editor/state.js"
COVERAGE = ROOT / "docs/QUICKEDIT_COVERAGE.md"


def _selection_block(src):
    start = src.index("// --- Selection commands (deterministic, no model call) ---")
    end = src.index("// --- Align (deterministic) ---", start)
    return src[start:end]


def test_save_branch_matches_save_or_store_and_parses_an_optional_name():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert "if (/\\b(?:save|store)\\b/.test(p)) {" in block
    # Optional "as <name>" / "named <name>" / "called <name>".
    assert "const nm = p.match(/\\b(?:as|named?|called)\\s+([^.,;]+?)\\s*$/i);" in block


def test_save_branch_calls_the_helper_and_reports_status():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert "if (_saveNamedSelection(name)) {" in block
    assert 'const count = (state.savedSelections || []).length;' in block
    assert "_setAiCommandStatus(`Saved the selection as “${name || `Selection ${count}`}”.`, 'done');" in block
    assert "_setAiCommandStatus('There is no active selection to save.', '');" in block


def test_save_branch_precedes_select_all_and_follows_reselect():
    src = EDITOR.read_text(encoding="utf-8")
    reselect = src.index("if (/\\breselect\\b|\\bbring\\s+back\\s+(?:the\\s+)?selection\\b")
    save = src.index("if (/\\b(?:save|store)\\b/.test(p)) {", reselect)
    select_all = src.index("if (/\\bselect\\s+(?:all|everything)\\b", save)
    assert reselect < save < select_all


def test_save_named_selection_infrastructure_exists():
    src = EDITOR.read_text(encoding="utf-8")
    assert "function _saveNamedSelection(name) {" in src
    assert "const mask = _selectionMaskAsDocument({ materializeLasso: true });" in src
    assert "if (!mask) return false;" in src
    assert "state.savedSelections.push({" in src
    assert "_schedulePersist();" in src
    state_src = STATE.read_text(encoding="utf-8")
    assert "savedSelections: []," in state_src


def test_coverage_doc_marks_save_and_view_as_ok():
    doc = COVERAGE.read_text(encoding="utf-8")
    assert "| Select → Save | \"save the selection\" | `_saveNamedSelection` | ✔ | ✔ | ✔ | OK |" in doc
    assert "| View → Rulers/Grid/Snap/Clear guides | \"show the grid\" | `#ge-view-menu` | ✔ | ✔ | ✔ | OK |" in doc
