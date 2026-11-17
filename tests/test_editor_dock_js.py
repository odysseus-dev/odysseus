from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCK = ROOT / "static/js/editor/build/dock.js"
RIGHT_PANEL = ROOT / "static/js/editor/build/right-panel.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
CSS = ROOT / "static/css/13-image-editor.css"
SW = ROOT / "static/sw.js"


def test_dock_module_shape():
    src = DOCK.read_text(encoding="utf-8")
    assert "export function buildDock" in src
    assert "ge-dock-tabbar" in src
    assert "ge-dock-panels" in src
    assert "ge-dock-panel" in src
    # A compact selector, not a wrapping tab strip that ellipsises.
    assert "ge-dock-select" in src
    assert "createElement('select')" in src
    # Entries may be grouped under Photoshop-style <optgroup> headers.
    assert "createElement('optgroup')" in src
    assert "optgroup.label = group" in src
    # Switching only toggles `hidden`; elements are re-parented, never cloned.
    assert "panel.appendChild(element)" in src
    assert "localStorage" in src


def test_right_panel_mounts_dock_on_desktop():
    src = RIGHT_PANEL.read_text(encoding="utf-8")
    assert "import { buildDock } from './dock.js';" in src
    assert "const useDock = window.innerWidth > 700;" in src
    assert "buildDock({" in src
    # Tool Options moved out of the dock into the options bar, so the dock
    # is reserved for Color / Adjustments / Panels; the controls panel is
    # still built here and returned for the caller to mount.
    assert "id: 'options', label: 'Tool Options'" not in src
    assert "controlsHTML" in src
    # Layers is pinned below the dock (always visible), not a tab.
    assert "rightPanel.appendChild(layerPanel);" in src
    assert "id: 'layers', label: 'Layers'" not in src
    # Mobile path must stay intact (no dock).
    assert "if (!useDock) rightPanel.appendChild(controls);" in src
    assert "if (!useDock) rightPanel.appendChild(layerPanel);" in src


def test_gallery_switches_dock_to_options_on_tool_select():
    src = GALLERY.read_text(encoding="utf-8")
    assert "layerPanel, dock } = buildRightPanel({" in src
    # Tool Options moved to the options bar, so selecting the move/transform
    # tools reveals the Properties group (where X/Y/W/H now live) instead.
    assert "if (dock && (toolId === 'move' || toolId === 'transform')) dock.show('properties');" in src


def test_gallery_registers_each_dock_panel_once():
    # A duplicated entry produced a duplicate tab and an always-visible
    # empty panel wrapper (the bug this redesign fixes).
    src = GALLERY.read_text(encoding="utf-8")
    assert src.count("id: 'actions', label: 'Actions'") == 1


def test_dock_css_present():
    css = CSS.read_text(encoding="utf-8")
    for token in (
        ".ge-right-panel--dock",
        ".ge-dock-tabbar",
        ".ge-dock-select",
        ".ge-dock-panels",
        ".ge-dock-panel[hidden]",
        # Photoshop-style stacked panel groups.
        ".ge-dock-group",
        ".ge-dock-tabs",
        ".ge-dock-tab",
        ".ge-dock-collapse",
        ".ge-dock-divider",
        ".ge-dock-group.is-collapsed",
        ".ge-dock--compact",
    ):
        assert token in css, token


def test_panel_group_divider_resizes_with_a_fixed_basis():
    src = DOCK.read_text(encoding="utf-8")
    assert "function wireDivider(divider, groupIndex) {" in src
    # The dragged group must keep an exact, non-shrinking basis; a `1 1` basis
    # lets the height-constrained dock shrink it back to 0 (dead divider).
    assert "target.style.flex = `0 0 ${Math.round(next)}px`;" in src
    assert "1 1 ${Math.round(next)}px" not in src


def test_status_bar_and_swatch_markup():
    topbar = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
    assert "ge-status-tool" in topbar
    toolbar = (ROOT / "static/js/editor/build/toolbar.js").read_text(encoding="utf-8")
    for token in ("ge-tool-swatches", "ge-swatch-fg", "ge-swatch-bg", "ge-swap-colors",
                  "id=\"ge-fg-color\"", "id=\"ge-bg-color\""):
        assert token in toolbar, token


def test_3d_and_plugins_stubs_are_disabled():
    topbar = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
    # Every 3D and Plugins item must be greyed out, not fire a toast.
    import re
    for action in ("from-file", "from-layer", "render", "print", "about"):
        m = re.search(r'data-3d-action="%s"[^>]*>' % re.escape(action), topbar)
        assert m and "disabled" in m.group(0), action
    for action in ("browse", "manage", "script"):
        m = re.search(r'data-plugins-action="%s"[^>]*>' % re.escape(action), topbar)
        assert m and "disabled" in m.group(0), action


def test_dock_module_is_precached():
    sw = SW.read_text(encoding="utf-8")
    assert "'/static/js/editor/build/dock.js'" in sw


def test_dock_divider_double_click_expands_and_restores():
    src = DOCK.read_text(encoding="utf-8")
    assert "divider.addEventListener('dblclick'" in src
    assert "target.dataset.dockExpanded === '1'" in src
    assert "target.dataset.dockPrevSize = String(" in src
    assert "target.dataset.dockExpanded = '1';" in src
    # a plain click from the double-click must not clear the expand flag
    assert "let moved = false;" in src
    assert "if (!moved) { moved = true;" in src
