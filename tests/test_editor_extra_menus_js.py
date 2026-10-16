from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
WIRE = (ROOT / "static/js/editor/wire-extra-menus.js").read_text(encoding="utf-8")
WIRE_TOPBAR = (ROOT / "static/js/editor/wire-topbar.js").read_text(encoding="utf-8")
GALLERY = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")
STYLE = app_css()


def test_topbar_exposes_layer_type_window_menus_in_photocraft_order():
    ids = [
        "ge-image-menu-btn",
        "ge-layer-menu-btn",
        "ge-type-menu-btn",
        "ge-selection-menu-btn",
        "ge-filter-menu-btn",
        "ge-view-menu-btn",
        "ge-window-menu-btn",
    ]
    positions = [TOPBAR.index(f'id="{i}"') for i in ids]
    assert positions == sorted(positions)
    for menu_id in ("ge-layer-menu", "ge-type-menu", "ge-window-menu"):
        assert f'id="{menu_id}"' in TOPBAR


def test_layer_menu_items_delegate_to_existing_controls():
    for action in ("new", "duplicate", "delete", "rasterize", "merge-down", "merge-all", "flatten", "select-all", "group", "fill"):
        assert f'data-layer-action="{action}"' in TOPBAR
    for target in (
        "ge-add-layer",
        "ge-merge-down",
        "ge-merge-all",
        "ge-select-all-layers",
        "ge-group-selected",
        "ge-layer-fill",
    ):
        assert f"click('{target}')" in WIRE
    # new layer items reuse the control API (single source of truth)
    for op in ("layers.duplicate", "layers.delete", "layers.rasterize", "layers.flatten"):
        assert f"window.geEditorControl?.invoke('{op}'" in WIRE
    # Photoshop shortcuts shown on the merge/duplicate/delete rows
    assert "Ctrl+J" in TOPBAR
    assert "Ctrl+E" in TOPBAR
    assert "Flatten Image" in TOPBAR


def test_mobile_hides_shortcut_hints_and_widens_resolution_input():
    assert ".ge-canvas-resolution-field input[type=\"number\"] { width: 84px; }" in STYLE
    assert ".ge-topbar .dropdown .dropdown-shortcut { display: none; }" in STYLE


def test_type_and_window_menus_wire_to_existing_surfaces():
    assert 'data-type-action="add"' in TOPBAR
    assert "document.querySelector('[data-tool=\"text\"]')?.click()" in WIRE
    for action in ("options", "layers", "history", "shortcuts"):
        assert f'data-window-action="{action}"' in TOPBAR
    assert "togglePanel?.(action)" in WIRE
    assert "click('ge-history-btn')" in WIRE
    assert "click('ge-shortcuts-btn')" in WIRE


def test_extra_menus_are_precached_and_mounted():
    assert "'/static/js/editor/wire-extra-menus.js'" in SW
    assert "import { wireExtraMenus } from './editor/wire-extra-menus.js';" in GALLERY
    assert "wireExtraMenus({" in GALLERY
    assert "export function wireExtraMenus({ closeOtherTopbarMenus, registerDocClickAway, dock, combineShapes, workspace, uiModule, exportLayeredPsd, syncWindowChecks, togglePanel })" in WIRE


def test_cross_menu_close_knows_new_menus():
    for menu_id in ("ge-layer-menu", "ge-type-menu", "ge-window-menu"):
        assert f"'{menu_id}'" in WIRE_TOPBAR


def test_css_positions_new_menu_wrappers():
    for cls in (".ge-layer-wrap", ".ge-type-wrap", ".ge-window-wrap"):
        assert cls in STYLE
