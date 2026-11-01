"""Quick Edit can colour-invert the image (Photoshop Image ▸ Adjustments ▸ Invert).

Photoshop's Invert (Cmd/Ctrl+I) colour-inverts the active layer. The command box
had no handling for it, so "invert the colours" was swallowed by the Select ▸
Inverse branch and inverted the *selection* instead. It now routes to the real
`invert` adjustment before the selection block, while "invert the selection"
still reaches Select ▸ Inverse.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
ADJ = (ROOT / "static" / "js" / "editor" / "fx" / "adj-popup.js").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_invert_routing_present():
    assert r"if (/\binvert\b[^.]*\b(?:colou?rs?|tones?|image|photo|picture)\b/.test(p)" in EDITOR
    assert r"|| /\b(?:colou?rs?|image|photo|picture)\b[^.]*\binvert\b/.test(p)) {" in EDITOR
    assert "await _runQuickControlOp('adjustments.apply', { type: 'invert' });" in EDITOR
    assert "'Inverted the colours.'" in EDITOR


def test_routing_precedes_selection_invert():
    routing = EDITOR.index("await _runQuickControlOp('adjustments.apply', { type: 'invert' });")
    assert routing < EDITOR.index("// --- Selection commands (deterministic, no model call) ---")
    assert routing < EDITOR.index("await _runQuickControlOp('selection.invert', {});")


def test_invert_adjustment_is_supported():
    assert "{ type: 'invert'," in ADJ


def test_coverage_doc_marks_invert_ok():
    row = next(line for line in COVERAGE.splitlines() if "Adjustments → Invert" in line)
    assert "OK" in row
    assert "fast path" in row
