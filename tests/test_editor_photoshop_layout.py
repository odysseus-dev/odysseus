"""Photoshop-parity layout checks: menu bar order/left alignment and the
document tab strip. These assert against the source + the full CSS cascade
the browser loads (via tests.helpers.stylesheets.app_css)."""
from pathlib import Path

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
GALLERY = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
STYLE = app_css()


# Photoshop's top-level menu order. 3D and Plugins sit between Filter/View
# and View/Window respectively.
_MENU_BUTTONS = [
    'id="ge-file-menu-btn"',
    'id="ge-edit-menu-btn"',
    'id="ge-image-menu-btn"',
    'id="ge-layer-menu-btn"',
    'id="ge-type-menu-btn"',
    'id="ge-selection-menu-btn"',
    'id="ge-filter-menu-btn"',
    'id="ge-3d-menu-btn"',
    'id="ge-view-menu-btn"',
    'id="ge-plugins-menu-btn"',
    'id="ge-window-menu-btn"',
    'id="ge-help-menu-btn"',
]


def test_menu_bar_present_in_photoshop_order():
    positions = [TOPBAR.index(marker) for marker in _MENU_BUTTONS]
    assert positions == sorted(positions), "menu buttons are out of Photoshop order"


def test_menu_bar_is_left_aligned_before_the_action_group():
    left = TOPBAR.index('<div class="ge-topbar-left">')
    right = TOPBAR.index('<div class="ge-topbar-right">')
    first_menu = TOPBAR.index(_MENU_BUTTONS[0])
    last_menu = TOPBAR.index(_MENU_BUTTONS[-1])
    # Every menu lives inside the left group; the action buttons follow.
    assert left < first_menu < last_menu < right
    assert 'id="ge-import-topbar"' in TOPBAR[right:]
    assert 'id="ge-save-menu-btn"' in TOPBAR[right:]
    assert 'ge-alpha-badge' in TOPBAR[right:]


def test_new_3d_and_plugins_menus_have_dropdowns():
    assert 'id="ge-3d-menu"' in TOPBAR
    assert 'id="ge-plugins-menu"' in TOPBAR
    assert 'data-3d-action=' in TOPBAR
    assert 'data-plugins-action=' in TOPBAR


def test_import_and_save_live_in_the_topbar_row():
    # The separate title-bar strip is gone; Import/Save now sit in the topbar
    # row (with File/Edit/…), and maximize moves up into the modal header
    # beside the window's minimize (_) and close (×) buttons.
    assert "docTabs.className = 'ge-doc-tabs'" not in GALLERY
    assert "topBar.querySelector('.ge-topbar-right') || topBar" in GALLERY
    assert "topbarRight.appendChild(importBtn)" in GALLERY
    assert "topbarRight.appendChild(saveWrap)" in GALLERY
    assert "topBar.querySelector('#ge-maximize-toggle')" in GALLERY
    assert "'modal-minimize-btn', 'ge-header-max-btn'" in GALLERY
    assert "ge-win-btn ge-win-close ge-doc-tab-close" not in GALLERY
    assert "ge-editor-minimize" not in GALLERY
    assert "ge-header-max-btn svg { top: 0 !important; transform: none; }" in STYLE


def test_save_menu_has_a_png_option():
    wire = (ROOT / "static/js/editor/wire-topbar.js").read_text(encoding="utf-8")
    assert 'id="ge-export-png"' in TOPBAR
    assert "export async function exportPNGFile()" in GALLERY
    assert "exportPNGFile, saveProject" in wire or "exportPNGFile," in wire
    assert "document.getElementById('ge-export-png')?.addEventListener('click', exportPNGFile);" in wire


def test_document_tab_and_menu_css_present():
    for token in (
        ".ge-doc-tabs",
        ".ge-doc-tab.is-active",
        ".ge-doc-tab-close",
        ".ge-3d-menu",
        ".ge-plugins-menu",
    ):
        assert token in STYLE, token


def test_multi_document_tabs_render_in_the_gallery_tab_bar():
    # Open documents are surfaced as tabs in the gallery tab bar (one per doc),
    # with the active one as the primary #gallery-editor-tab and a "+" spawner.
    gallery_js = (ROOT / "static/js/gallery.js").read_text(encoding="utf-8")
    assert 'id="gallery-editor-tabs-extra"' in gallery_js
    assert "window.geEditorDocTabs = _renderEditorDocTabs" in GALLERY
    assert "const _renderEditorDocTabs = () => {" in GALLERY
    assert "window.geEditorControl?.invoke('document.switch', { id: doc.id })" in GALLERY
    assert "window.geEditorControl?.invoke('document.create', {});" in GALLERY
    assert "gallery-tab gallery-tab-new" in GALLERY
    assert ".gallery-editor-tabs-extra { display: contents; }" in STYLE
    assert "window.geEditorDocTabs?.();" in GALLERY
