"""Window > Workspace submenu: a nested flyout with the window setups.

The Window dropdown used to list the workspace presets as flat rows. They are
now a `.ge-menu-parent` flyout ("Workspace") whose children set up the editor
windows (dock panels + tab + right-panel width). The submenu open/close toggle
is the shared `wireSubmenuToggles` helper (also used by Image/Filter/Selection).
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
WIRE = (ROOT / "static/js/editor/wire-extra-menus.js").read_text(encoding="utf-8")
MENUS = (ROOT / "static/js/editor/wire-topbar-menus.js").read_text(encoding="utf-8")
SUBMENU = (ROOT / "static/js/editor/submenu-toggles.js").read_text(encoding="utf-8")
WORKSPACES = (ROOT / "static/js/editor/workspaces.js").read_text(encoding="utf-8")
GALLERY = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_window_menu_has_a_workspace_flyout_parent():
    assert 'class="dropdown-item-compact ge-menu-parent" data-submenu="window-workspace"' in TOPBAR
    assert '<div class="ge-submenu" data-submenu-panel="window-workspace">' in TOPBAR
    # the variants still carry their window-action ids
    for name in ("essentials", "photography", "painting", "graphic", "minimal"):
        assert f'data-window-action="workspace-{name}"' in TOPBAR


def test_submenu_toggle_helper_is_shared():
    assert "export function wireSubmenuToggles(menu) {" in SUBMENU
    assert "import { wireSubmenuToggles } from './submenu-toggles.js';" in MENUS
    assert "import { wireSubmenuToggles } from './submenu-toggles.js';" in WIRE
    # the Window menu (wired in wire-extra-menus) gets the toggle
    assert "wireSubmenuToggles(menu);" in WIRE
    assert "wireSubmenuToggles(menu);" in MENUS


def test_workspaces_set_up_the_windows():
    # every preset declares the panels it shows
    assert WORKSPACES.count("panels:") == 5
    assert "setPanelVisible" in WORKSPACES
    assert "PANEL_IDS" in WORKSPACES
    # the "minimal" preset is just layers + history
    assert "minimal: Object.freeze({ label: 'Minimal'" in WORKSPACES
    assert "panels: Object.freeze(['layers', 'history'])" in WORKSPACES
    # composer hands the panel-visibility setter into applyWorkspace
    assert "setPanelVisible: (id, visible) => panelVisibility.setVisible(id, visible)" in GALLERY


def test_active_workspace_is_remembered_and_restored_on_open():
    # the chosen workspace is restored from localStorage when the editor opens
    assert "localStorage.getItem('ge-active-workspace')" in GALLERY
    restore = GALLERY[GALLERY.index("Remember the workspace/window layout"):]
    assert "_applyWorkspace(savedWorkspace" in restore
    assert "rightPanel: window.innerWidth > 700 ? rightPanel : null" in restore


def test_workspace_flyout_releases_the_menu_clip():
    assert ".ge-window-menu:has(.ge-menu-parent.open)" in CSS


def test_submenu_helper_is_precached_and_cache_bumped():
    assert "'/static/js/editor/submenu-toggles.js'" in SW
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
