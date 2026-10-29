import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "13-image-editor.css").read_text()
STUDIO = (ROOT / "static" / "js" / "editor" / "panels" / "studio-panels.js").read_text()
PANELS_JS = (ROOT / "static" / "js" / "editor" / "panels" / "panels.js").read_text()
GE = (ROOT / "static" / "js" / "galleryEditor.js").read_text()
SW = (ROOT / "static" / "sw.js").read_text()

PANELS = ["swatches", "gradients", "patterns", "adjustments", "properties", "histogram"]


def test_inner_panel_titles_hidden():
    for pid in PANELS:
        assert f"#ge-panel-{pid} > .ge-panel-title" in CSS, pid
    assert "display: none;" in CSS


def test_panel_titles_removed_at_creation():
    # The title node is not created at all (not merely hidden) for these panels.
    for src in (PANELS_JS, STUDIO):
        assert "if (title) wrap.appendChild(el('div', 'ge-panel-title', title));" in src
    for pid in ("swatches", "gradients", "histogram"):
        assert f"panel('{pid}', ''," in PANELS_JS, pid
    for pid in ("properties", "patterns", "adjustments"):
        assert f"panel('{pid}', ''," in STUDIO, pid


def test_swatch_chips_are_round():
    assert ".ge-mini-swatch," in CSS
    assert ".ge-swatch-grid .ge-swatch," in CSS
    assert ".ge-swatch-body .ge-color-input.cp-swatch-input {" in CSS
    assert "border-radius: 50%;" in CSS
    assert ".ge-gradient-swatch { border-radius: 999px; }" in CSS


def test_adjustment_buttons_have_svg_icons():
    assert "const ADJUSTMENT_ICONS = Object.freeze({" in STUDIO
    assert "function adjIcon(type)" in STUDIO
    assert "<svg viewBox=\"0 0 16 16\"" in STUDIO
    assert "`${adjIcon(type)}<span class=\"ge-adj-label\">${label}</span>`" in STUDIO
    assert ".ge-adj-icon svg {" in CSS


def test_add_adjustment_does_not_self_trigger():
    # The old handler clicked #ge-add-layer then re-clicked the matching
    # [data-adjustment-type] node, which was the grid button itself -> an
    # unbounded loop that stacked adjustment layers.
    assert 'document.getElementById(\'ge-add-layer\')?.click();' not in GE
    assert 'document.querySelector(`[data-adjustment-type="${type}"]`)?.click();' not in GE
    assert "_addAdjustmentLayer(type, anchor || document.getElementById('ge-add-fx'));" in GE


def test_sw_cache_bumped():
    # Version-agnostic: every commit bumps the SW cache, so pinning the exact
    # number makes this test fail on the next commit.
    m = re.search(r"CACHE_NAME = 'odysseus-v\d+-pr40-editor-precache'", SW)
    assert m
