"""Window > Workspace submenu: a nested flyout with 4 window setups.

The Window dropdown used to list the 4 workspace presets as flat rows. They are
now a `.ge-menu-parent` flyout ("Workspace") whose 4 children set up the editor
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
    # the 4 variants still carry their window-action ids
    for name in ("essentials", "photography", "painting", "graphic"):
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
    assert WORKSPACES.count("panels:") == 4
    assert "setPanelVisible" in WORKSPACES
    assert "PANEL_IDS" in WORKSPACES
    # composer hands the panel-visibility setter into applyWorkspace
    assert "setPanelVisible: (id, visible) => panelVisibility.setVisible(id, visible)" in GALLERY


def test_workspace_flyout_releases_the_menu_clip():
    assert ".ge-window-menu:has(.ge-menu-parent.open)" in CSS


def test_submenu_helper_is_precached_and_cache_bumped():
    assert "'/static/js/editor/submenu-toggles.js'" in SW
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
