"""Marker tests for the Photoshop-style nested submenus in the editor topbar.

The Filter menu groups (Blur, Sharpen, Noise, Pixelate, Stylize, Distort,
Render, Other, Presets, Retained effects) and Image > Adjustments are rendered
as `.ge-menu-parent` rows that reveal a `.ge-submenu` flyout panel. These tests
read the source files as text and assert the markup/CSS contract.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text()
CSS = (ROOT / "static/css/13-image-editor.css").read_text()
SW = (ROOT / "static/sw.js").read_text()


def test_parent_markup_present():
    assert "ge-menu-parent" in TOPBAR
    assert 'data-submenu="filter-blur"' in TOPBAR
    assert 'data-submenu-panel="filter-blur"' in TOPBAR


def test_at_least_six_submenu_panels():
    panels = re.findall(r'data-submenu-panel="([^"]+)"', TOPBAR)
    assert len(panels) >= 6, panels


def test_children_keep_filter_action_ids():
    assert 'data-filter-action="blur-gaussian"' in TOPBAR


def test_submenu_css_present():
    assert ".ge-submenu {" in CSS
    assert ".ge-menu-parent" in CSS


def test_service_worker_version_agnostic():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
