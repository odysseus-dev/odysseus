"""Quick Edit support for Photoshop's Type ▸ Warp ("warp the text").

The editor already had a full retained-text warp pipeline (static/js/editor/
text-layer.js `warpCanvas`, and `warp`/`warpAmount` on the text data), but the
Quick Edit client fast path never routed a warp request, so "warp the text"
fell through to "No built-in quick action matched". These tests pin the new
deterministic branch in galleryEditor.js `_executeQuickEditLocal`.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EDITOR = (ROOT / "static" / "js" / "galleryEditor.js").read_text(encoding="utf-8")
TEXT_LAYER = (ROOT / "static" / "js" / "editor" / "text-layer.js").read_text(encoding="utf-8")
COVERAGE = (ROOT / "docs" / "QUICKEDIT_COVERAGE.md").read_text(encoding="utf-8")


def test_warp_branch_applies_style_and_amount():
    assert "// --- Type ▸ Warp (deterministic) ---" in EDITOR
    assert "const _warpWord = /\\bwarp(?:ed)?\\b/.test(p);" in EDITOR
    assert "const _warpTextWord = /\\b(?:text|title|caption|heading|word|lettering|type)\\b/.test(p);" in EDITOR
    assert "_rerenderTextLayer(_wLayer, { ..._wLayer.text, warp: style, warpAmount: _wAmount });" in EDITOR
    assert "_saveState('Warp text');" in EDITOR
    assert "_setAiCommandStatus(style === 'none' ? 'Removed the text warp.' : `Warped the text (${style}).`, 'done');" in EDITOR


def test_warp_maps_every_photoshop_style():
    for style in ("arc", "arch", "bulge", "inflate", "squeeze", "fisheye", "wave", "rise", "twist", "flag"):
        assert f"style = '{style}'" in EDITOR, style
    assert "style = 'none'" in EDITOR
    assert "const _wAm = p.match(/\\b(?:by|amount|to)\\s*(-?\\d{1,3})\\b/);" in EDITOR
    assert "Math.max(-100, Math.min(100, Math.round(Number(_wAm[1])))) : 50" in EDITOR


def test_warp_runs_before_generic_text_edit():
    warp_idx = EDITOR.index("// --- Type ▸ Warp (deterministic) ---")
    text_idx = EDITOR.index("// --- Text edit (deterministic) ---")
    assert warp_idx < text_idx


def test_warp_infrastructure_exists():
    assert "export function warpCanvas(base, style, amount)" in TEXT_LAYER
    assert "export const WARP_STYLES" in TEXT_LAYER
    assert "warp: WARP_STYLES.includes(value.warp) ? value.warp : 'none'," in TEXT_LAYER
    assert "warpAmount: finite(value.warpAmount, DEFAULT_TEXT_DATA.warpAmount, -100, 100)," in TEXT_LAYER
    assert "function _rerenderTextLayer(layer, nextText, preserveCenter = true)" in EDITOR


def test_coverage_row_marked_ok():
    assert "| Type → Warp | \"warp the text\" | `_rerenderTextLayer` (warp) | ✔ | ✔ | ✔ | OK |" in COVERAGE
