from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


WORKSPACES = read("static/js/editor/workspaces.js")
TOPBAR = read("static/js/editor/build/topbar.js")
WIRE = read("static/js/editor/wire-extra-menus.js")
GALLERY = read("static/js/galleryEditor.js")
SW = read("static/sw.js")


def test_workspaces_module_shape():
    assert "export const WORKSPACES" in WORKSPACES
    assert "export function applyWorkspace(" in WORKSPACES
    for name in ("essentials", "photography", "painting", "graphic"):
        assert name in WORKSPACES
    assert "localStorage" in WORKSPACES


def test_window_menu_exposes_workspaces_and_panels():
    for action in (
        "workspace-essentials",
        "workspace-photography",
        "workspace-painting",
        "workspace-graphic",
        "swatches",
        "gradients",
        "channels",
        "histogram",
        "info",
        "navigator",
    ):
        assert f'data-window-action="{action}"' in TOPBAR


def test_wire_extra_menus_dispatches_workspaces():
    assert "workspace" in WIRE
    assert "action.startsWith('workspace-')" in WIRE


def test_gallery_wires_workspaces():
    assert "from './editor/workspaces.js';" in GALLERY
    assert "_applyWorkspace(" in GALLERY
    assert "workspace: (name) =>" in GALLERY


def test_workspaces_module_is_precached():
    assert "/static/js/editor/workspaces.js" in SW
