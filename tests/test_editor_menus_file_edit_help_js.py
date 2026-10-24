from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_topbar_has_file_edit_help_menus():
    src = read("static/js/editor/build/topbar.js")
    for token in [
        'id="ge-file-menu"',
        'id="ge-file-menu-btn"',
        'id="ge-edit-menu"',
        'id="ge-edit-menu-btn"',
        'id="ge-help-menu"',
        'id="ge-help-menu-btn"',
        'data-file-action="import"',
        'data-file-action="save"',
        'data-file-action="save-project"',
        'data-edit-action="undo"',
        'data-edit-action="redo"',
        'data-edit-action="history"',
        'data-help-action="shortcuts"',
        'data-help-action="about"',
    ]:
        assert token in src


def test_topbar_menu_order_is_photocraft_order():
    src = read("static/js/editor/build/topbar.js")
    order = [
        'id="ge-file-menu"',
        'id="ge-edit-menu"',
        'id="ge-image-menu"',
        'id="ge-layer-menu"',
        'id="ge-type-menu"',
        'id="ge-selection-menu"',
        'id="ge-filter-menu"',
        'id="ge-view-menu"',
        'id="ge-window-menu"',
        'id="ge-help-menu"',
    ]
    positions = [src.index(token) for token in order]
    assert positions == sorted(positions)


def test_extra_menus_wire_file_edit_help():
    src = read("static/js/editor/wire-extra-menus.js")
    for token in [
        "[data-file-action],[data-edit-action],[data-help-action]",
        "bind('ge-file-menu-btn', 'ge-file-menu'",
        "bind('ge-edit-menu-btn', 'ge-edit-menu'",
        "bind('ge-help-menu-btn', 'ge-help-menu'",
        "uiModule",
    ]:
        assert token in src


def test_topbar_menu_ids_include_file_edit_help():
    src = read("static/js/editor/wire-topbar.js")
    for token in [
        "'ge-file-menu'",
        "'ge-edit-menu'",
        "'ge-help-menu'",
        "'ge-file-menu-btn'",
        "'ge-edit-menu-btn'",
        "'ge-help-menu-btn'",
    ]:
        assert token in src


def test_css_has_menu_wraps():
    src = read("static/css/13-image-editor.css")
    for token in [".ge-file-wrap", ".ge-edit-wrap", ".ge-help-wrap"]:
        assert token in src


MENUS = ["file", "edit", "image", "layer", "type", "selection", "filter", "view", "window", "help"]


def test_every_dropdown_menu_uses_a_group_label():
    src = read("static/js/editor/build/topbar.js")
    starts = {name: src.index('id="ge-%s-menu"' % name) for name in MENUS}
    for name in MENUS:
        start = starts[name]
        nxt = min([pos for pos in starts.values() if pos > start] + [len(src)])
        block = src[start:nxt]
        assert "ge-menu-label" in block or "ge-filter-submenu-label" in block, name


def test_every_dropdown_menu_has_no_duplicate_actions():
    src = read("static/js/editor/build/topbar.js")
    checks = [
        ('data-file-action="export-psd"', 1),
        ('data-file-action="export"', 1),
        ('data-file-action="save"', 1),
        ('data-file-action="save-copy"', 1),
        ('data-window-action="actions"', 1),
        ('data-window-action="history"', 1),
        ('data-window-action="shortcuts"', 1),
        ('data-layer-action="fill"', 1),
    ]
    for token, expected in checks:
        assert src.count(token) == expected, (token, src.count(token))


def test_css_shared_menu_classes_cover_all_dropdowns():
    src = read("static/css/13-image-editor.css")
    for name in ["file", "edit", "layer", "type", "window", "help"]:
        assert ".ge-%s-menu" % name in src
    assert ".ge-filter-submenu-label,\n.ge-menu-label {" in src
