from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PANELS = ROOT / "static/js/editor/panels/panels.js"
RIGHT_PANEL = ROOT / "static/js/editor/build/right-panel.js"
GALLERY = ROOT / "static/js/galleryEditor.js"
SW = ROOT / "static/sw.js"


def read(path):
    return path.read_text(encoding="utf-8")


def test_color_panels_module_shape():
    src = read(PANELS)
    assert "export function createColorPanels(" in src
    for panel_id in ("swatches", "gradients", "channels", "histogram", "info", "navigator"):
        assert f"ge-panel-${{id}}" in src
        assert f"'{panel_id}'" in src, panel_id
    assert "applyCanvasPan" in src
    assert "drawHistogram" in src


def test_right_panel_spreads_extra_panels():
    src = read(RIGHT_PANEL)
    assert "export function buildRightPanel({ controlsHTML, layerPanelHTML, extraPanels = [] }) {" in src
    assert "...(Array.isArray(extraPanels) ? extraPanels : [])," in src


def test_gallery_builds_color_panels():
    src = read(GALLERY)
    assert "import { createColorPanels } from './editor/panels/panels.js';" in src
    assert "const colorPanels = createColorPanels({" in src
    assert "extraPanels: [" in src
    for panel_id in ("swatches", "gradients", "channels", "histogram", "info", "navigator"):
        assert f"element: colorPanels.{panel_id}" in src, panel_id


def test_color_panels_module_is_precached():
    assert "/static/js/editor/panels/panels.js" in read(SW)
