from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "static/js/galleryEditor.js"
COVERAGE = ROOT / "docs/QUICKEDIT_COVERAGE.md"


def _selection_block(src):
    start = src.index("// --- Selection commands (deterministic, no model call) ---")
    end = src.index("// --- Align (deterministic) ---", start)
    return src[start:end]


def test_load_branch_matches_load_and_parses_an_optional_name():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert "if (/\\bload\\b/.test(p)) {" in block
    # Name via "as <name>" / "named <name>" / "called <name>".
    assert "const nm = p.match(/\\b(?:as|named?|called)\\s+([^.,;]+?)\\s*$/i);" in block
    # Or the "<name> selection" word order ("load the sky selection").
    assert "const m2 = p.match(/\\bload\\s+(?:the\\s+|my\\s+)?([a-z0-9 _-]+?)\\s+selection\\b/i);" in block


def test_load_branch_resolves_a_target_and_falls_back():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert "const saved = state.savedSelections || [];" in block
    assert "saved.find(s => s.name.toLowerCase() === lc) || saved.find(s => lc.includes(s.name.toLowerCase()))" in block
    assert "if (!target && saved.length === 1) target = saved[0];" in block
    assert "if (!target) target = saved[saved.length - 1];" in block
    assert "if (_loadNamedSelection(target.id)) {" in block


def test_load_branch_reports_status_and_hints_when_empty():
    src = EDITOR.read_text(encoding="utf-8")
    block = _selection_block(src)
    assert "_setAiCommandStatus(`Loaded the “${target.name}” selection.`, 'done');" in block
    assert "_setAiCommandStatus('Could not load that selection.', '');" in block
    assert "_setAiCommandStatus('There are no saved selections to load yet.', '');" in block


def test_load_branch_precedes_select_all_and_follows_save():
    src = EDITOR.read_text(encoding="utf-8")
    save = src.index("if (/\\b(?:save|store)\\b/.test(p)) {")
    load = src.index("if (/\\bload\\b/.test(p)) {", save)
    select_all = src.index("if (/\\bselect\\s+(?:all|everything)\\b", load)
    # Must precede the sky/subject branches so "load the selection as sky"
    # resolves the saved name instead of selecting the sky.
    sky = src.index("if (/\\bsky\\b/.test(p)) {", load)
    assert save < load < select_all < sky


def test_load_named_selection_infrastructure_exists():
    src = EDITOR.read_text(encoding="utf-8")
    assert "function _loadNamedSelection(id) {" in src
    assert "const saved = (state.savedSelections || []).find(item => item.id === id);" in src
    assert "return _activateSelectionCanvas(saved.canvas, 'saved');" in src
    assert "loadNamedSelection: _loadNamedSelection," in src


def test_coverage_doc_marks_load_and_reselect_as_ok():
    doc = COVERAGE.read_text(encoding="utf-8")
    assert "| Select → Load | \"load the selection as sky\" | `_loadNamedSelection` | ✔ | ✔ | ✔ | OK |" in doc
    assert "| Select → Reselect | \"reselect\" | `_reselectSelection` | ✔ | ✔ | ✔ | OK |" in doc
