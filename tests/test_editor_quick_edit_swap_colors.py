"""Quick Edit can swap / reset the foreground & background colours (Photoshop X / D).

Photoshop's colour swatches: X swaps the foreground and background, D resets them
to the default black foreground / white background. The command box previously
ignored "swap the colours" and "reset the colours"; these now route through the
same helpers the X / D shortcuts and the swatch buttons already use.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_swap_colours_routing_present():
    assert r"if (/\bswap\b[^.]*\b(?:colou?rs?|foreground|background)\b|\b(?:colou?rs?)\b[^.]*\bswap\b/.test(p)) {" in EDITOR
    assert "window.geSwapColors?.();" in EDITOR
    assert "'Swapped the foreground and background colours.'" in EDITOR


def test_reset_colours_routing_present():
    assert r"if (/\breset\b[^.]*\bcolou?rs?\b|\bdefault\b[^.]*\bcolou?rs?\b/.test(p)) {" in EDITOR
    assert "window.geResetColors?.();" in EDITOR
    assert "'Reset to the default colours.'" in EDITOR


def test_helpers_are_defined():
    assert "window.geSwapColors = _swapColors;" in EDITOR
    assert "window.geResetColors = () => {" in EDITOR


def test_routing_precedes_view_menu():
    swap = EDITOR.index(r"/\bswap\b[^.]*\b(?:colou?rs?|foreground|background)\b")
    view = EDITOR.index("// --- View menu (Photoshop View")
    assert swap < view


def test_coverage_doc_marks_swap_ok():
    assert "swap" in COVERAGE.lower()
    assert "colou" in COVERAGE.lower()
