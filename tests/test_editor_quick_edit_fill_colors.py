"""Quick Edit can fill with the foreground / background colour (Edit ▸ Fill ▸ Contents).

Photoshop's Edit ▸ Fill offers Contents = Foreground Color / Background Color.
Quick Edit previously only understood named colours ("fill the selection with
blue"); "fill the selection with the foreground color" fell through unmatched.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_fill_fg_bg_helper_resolves_state_colors():
    assert "const _fillFgBg =" in EDITOR
    assert r"/\b(?:with|using)\s+(?:the\s+)?foreground(?:\s*colou?r)?\b/.test(p) ? (state.color || '#000000')" in EDITOR
    assert r"/\b(?:with|using)\s+(?:the\s+)?background(?:\s*colou?r)?\b/.test(p) ? (state.bgColor || '#ffffff') : null;" in EDITOR


def test_canvas_fill_uses_fg_bg():
    assert "const fillAllColor = fillWith ? _colorForName(fillWith[1]) : (_fillFgBg || state.color || '#000000');" in EDITOR


def test_selection_fill_accepts_fg_bg():
    assert "&& (fillWith || _fillFgBg)) {" in EDITOR
    assert "const color = fillWith ? _colorForName(fillWith[1]) : _fillFgBg;" in EDITOR


def test_fill_helper_precedes_generative_guard():
    helper = EDITOR.index("const _fillFgBg =")
    guard = EDITOR.index("const _fillIsGenerative =")
    assert helper < guard


def test_coverage_doc_marks_fill_foreground_ok():
    assert "fill" in COVERAGE.lower()
    assert "foreground" in COVERAGE.lower()
