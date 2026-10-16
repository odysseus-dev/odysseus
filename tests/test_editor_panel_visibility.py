"""Static contract tests for the View > Panels visibility feature.

The panel-visibility module, its CSS, the View/Window menu markup, the wiring
and the control-op catalog are plain JavaScript, so these tests assert the
contract at the source level (the project's editor suites are contract/static,
not a JS runtime).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MODULE = ROOT / "static/js/editor/panel-visibility.js"
CSS = ROOT / "static/css/13-image-editor.css"
TOPBAR = ROOT / "static/js/editor/build/topbar.js"
VIEW_WIRE = ROOT / "static/js/editor/wire-view-menu.js"
EXTRA_WIRE = ROOT / "static/js/editor/wire-extra-menus.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
SW = ROOT / "static/sw.js"


def _read(path):
    return path.read_text(encoding="utf-8")


def test_module_registry_and_api():
    src = _read(MODULE)
    assert "export const PANELS = Object.freeze([" in src
    for pid in ["swatches", "gradients", "patterns", "properties", "adjustments",
                "histogram", "info", "channels", "paths", "character",
                "navigator", "history", "actions", "layers", "options"]:
        assert f"id: '{pid}'" in src, pid
    for fn in ["export function isVisible(id)",
               "export function list()",
               "export function apply()",
               "export function setVisible(id, visible)",
               "export function toggle(id)",
               "export function showAll()",
               "export function hideAll()",
               "export function reset()",
               "export function subscribe(fn)"]:
        assert fn in src, fn


def test_module_persists_and_tags_dom():
    src = _read(MODULE)
    assert "const STORAGE_KEY = 'ge-panel-visibility';" in src
    assert 'localStorage.setItem(STORAGE_KEY, JSON.stringify(map))' in src
    assert 'data-panel-hidden' in src
    # Tags the wrapper, the tab-strip item, the compact select option,
    # the collapsed group, the pinned Layers strip and the Tool Options bar.
    assert '.ge-dock-panel[data-panel="${id}"]' in src
    assert '.ge-dock-tab-item[data-panel="${id}"]' in src
    assert '.ge-dock-select option[value="${id}"]' in src
    assert '.ge-dock-group[data-group="${group}"]' in src
    assert "document.querySelector('.ge-layers')" in src
    assert "document.querySelector('.ge-controls')" in src


def test_css_hides_tagged_nodes():
    src = _read(CSS)
    assert '.ge-dock-panel[data-panel-hidden="1"]' in src
    assert '.ge-dock-tab-item[data-panel-hidden="1"]' in src
    assert '.ge-dock-group[data-panel-hidden="1"]' in src
    assert '.ge-dock-divider[data-panel-hidden="1"]' in src
    assert '.ge-layers[data-panel-hidden="1"]' in src
    assert '.ge-controls[data-panel-hidden="1"]' in src


def test_view_menu_has_panels_group_and_layout_actions():
    src = _read(TOPBAR)
    assert '<div class="ge-menu-label">Panels</div>' in src
    assert '<div class="ge-menu-label">Panel layout</div>' in src
    for pid in ["options", "layers", "swatches", "gradients", "patterns",
                "properties", "adjustments", "histogram", "info", "channels",
                "paths", "character", "navigator", "history", "actions"]:
        assert f'data-view-action="panel-{pid}"' in src, pid
    for action in ["panel-show-all", "panel-hide-all", "panel-reset"]:
        assert f'data-view-action="{action}"' in src, action


def test_view_menu_wiring():
    src = _read(VIEW_WIRE)
    assert "import * as panelVisibility from './panel-visibility.js';" in src
    assert '[data-view-action="panel-${panel.id}"]' in src
    assert "item?.classList.toggle('active', !!panel.visible);" in src
    assert "if (action === 'panel-show-all')" in src
    assert "if (action === 'panel-hide-all')" in src
    assert "if (action === 'panel-reset')" in src
    assert "if (action.startsWith('panel-'))" in src
    assert "panelVisibility.toggle(action.slice('panel-'.length))" in src


def test_window_menu_keeps_the_show_panel_contract():
    # The Window menu stays a "bring a panel to front / focus it" menu; panel
    # show/hide toggles live in the View menu (above). Guards against the two
    # menus claiming the same data-window-action values.
    src = _read(TOPBAR)
    for action in ["options", "layers", "properties", "character", "adjustments",
                   "swatches", "gradients", "channels", "paths", "patterns",
                   "histogram", "info", "navigator", "history", "actions"]:
        assert f'data-window-action="{action}"' in src, action
    assert 'data-window-action="panel-' not in src


def test_window_menu_wiring_targets_existing_surfaces():
    src = _read(EXTRA_WIRE)
    assert "togglePanel?.(action)" in src
    assert "click('ge-history-btn')" in src
    assert 'data-window-action="panel-' not in src
    assert "panelVisibility" not in src


def test_gallery_editor_mounts_and_exposes_control_ops():
    src = _read(GALLERY)
    assert "import * as panelVisibility from './editor/panel-visibility.js';" in src
    assert "panelVisibility.apply();" in src
    assert "window.gePanelVisibility = panelVisibility;" in src
    for op in ["panels.list", "panels.setVisible", "panels.toggle",
               "panels.showAll", "panels.hideAll", "panels.reset"]:
        assert f"'{op}':" in src, op
    assert "panelVisibility.setVisible(p.id, p.visible !== false)" in src
    assert "panelVisibility.toggle(p.id)" in src


def test_sw_cache_name_bumped():
    src = _read(SW)
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", src)
