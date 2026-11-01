"""Marker tests for the second batch of Photoshop-style editor commands.

These assert the wiring for Filter > Blur > Radial Blur, Filter > Noise >
Reduce Noise and Dust & Scratches, Layer > Merge Visible and Layer > New
Fill Layer, all of which reuse existing filter / merge / fill machinery.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text()
WIRE = (ROOT / "static/js/editor/wire-topbar-menus.js").read_text()
EXTRA = (ROOT / "static/js/editor/wire-extra-menus.js").read_text()
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text()


def test_filter_menu_items_present():
    for action in (
        'data-filter-action="radial-blur"',
        'data-filter-action="reduce-noise"',
        'data-filter-action="dust-scratches"',
    ):
        assert action in TOPBAR, action


def test_layer_menu_items_present():
    assert 'data-layer-action="merge-visible"' in TOPBAR
    assert 'data-layer-action="new-fill"' in TOPBAR


def test_filter_wire_branches_present():
    assert "action === 'radial-blur'" in WIRE
    assert "action === 'reduce-noise'" in WIRE
    assert "action === 'dust-scratches'" in WIRE
    assert "else if (action === 'radial-blur') applyZoomBlur?.();" in WIRE
    assert "else if (action === 'reduce-noise') applyDespeckle?.();" in WIRE
    assert "else if (action === 'dust-scratches') applyDespeckle?.();" in WIRE


def test_layer_wire_branches_present():
    assert "action === 'merge-visible'" in EXTRA
    assert "action === 'new-fill'" in EXTRA
    assert "else if (action === 'merge-visible') click('ge-merge-all');" in EXTRA
    assert "else if (action === 'new-fill') { click('ge-add-layer'); click('ge-layer-fill'); }" in EXTRA


def test_filter_aliases_present():
    assert "'radial-blur': 'blur-zoom'" in EDITOR
    assert "'reduce-noise': 'noise-despeckle'" in EDITOR
    assert "'dust-scratches': 'noise-despeckle'" in EDITOR
