from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_topbar_has_file_edit_help_menus():
    src = read("static/js/editor/build/topbar.js")
    for token in [
        'id="ge-file-menu-btn"',
        'id="ge-file-menu"',
        'id="ge-edit-menu-btn"',
        'id="ge-edit-menu"',
        'id="ge-help-menu-btn"',
        'id="ge-help-menu"',
        'data-file-action',
        'data-edit-action',
        'data-help-action',
    ]:
        assert token in src


def test_main_menu_bar_order():
    src = read("static/js/editor/build/topbar.js")
    order = [
        'id="ge-file-menu-btn"',
        'id="ge-edit-menu-btn"',
        'id="ge-image-menu-btn"',
        'id="ge-layer-menu-btn"',
        'id="ge-type-menu-btn"',
        'id="ge-selection-menu-btn"',
        'id="ge-filter-menu-btn"',
        'id="ge-view-menu-btn"',
        'id="ge-window-menu-btn"',
        'id="ge-help-menu-btn"',
    ]
    positions = [src.find(token) for token in order]
    assert all(pos >= 0 for pos in positions)
    assert positions == sorted(positions)


def test_wire_extra_menus_handles_file_edit_help():
    src = read("static/js/editor/wire-extra-menus.js")
    for token in [
        "ge-file-menu-btn",
        "ge-edit-menu-btn",
        "ge-help-menu-btn",
        "[data-file-action]",
        "[data-edit-action]",
        "[data-help-action]",
    ]:
        assert token in src


def test_wire_topbar_registers_all_menus():
    src = read("static/js/editor/wire-topbar.js")
    for token in ["'ge-file-menu'", "'ge-edit-menu'", "'ge-help-menu'"]:
        assert token in src
