from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_actions_panel_module_shape():
    src = read("static/js/editor/actions-panel.js")
    for token in [
        "export const ACTION_COMMANDS",
        "export function createActionsPanel(",
        "ge-panel-actions",
        "localStorage",
        "'ge-actions'",
    ]:
        assert token in src


def test_actions_panel_supports_batch():
    src = read("static/js/editor/actions-panel.js")
    for token in [
        "ge-actions-batch-row",
        "ge-actions-batch-select",
        "runOnAllLayers",
        "getLayers",
        "selectLayer",
    ]:
        assert token in src


def test_gallery_wires_actions_batch():
    src = read("static/js/galleryEditor.js")
    for token in ["getLayers: () =>", "selectLayer: (id) =>"]:
        assert token in src


def test_gallery_wires_actions_panel():
    src = read("static/js/galleryEditor.js")
    for token in [
        "from './editor/actions-panel.js'",
        "createActionsPanel(",
        "element: actionsPanel.element",
    ]:
        assert token in src


def test_window_menu_exposes_actions():
    src = read("static/js/editor/build/topbar.js")
    assert 'data-window-action="actions"' in src


def test_wire_extra_menus_dispatches_actions():
    src = read("static/js/editor/wire-extra-menus.js")
    assert "'actions'" in src


def test_actions_panel_css_present():
    src = read("static/css/13-image-editor.css")
    for token in [".ge-actions-step", ".ge-actions-name", ".ge-action-remove", ".ge-actions-batch-row"]:
        assert token in src


def test_actions_panel_is_precached():
    src = read("static/sw.js")
    assert "/static/js/editor/actions-panel.js" in src
