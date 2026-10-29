from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_studio_panels_module_shape():
    src = read("static/js/editor/panels/studio-panels.js")
    for token in [
        "export function createStudioPanels(",
        "export const ADJUSTMENT_TYPES",
        "panel('properties'",
        "panel('character'",
        "panel('paths'",
        "panel('patterns'",
        "panel('adjustments'",
        "panel('history'",
    ]:
        assert token in src


def test_studio_panels_has_adjustment_parity_types():
    src = read("static/js/editor/panels/studio-panels.js")
    for adj in [
        "invert",
        "posterize",
        "threshold",
        "photo-filter",
        "channel-mixer",
        "color-lookup",
    ]:
        assert adj in src


def test_gallery_wires_studio_panels():
    src = read("static/js/galleryEditor.js")
    for token in [
        "from './editor/panels/studio-panels.js'",
        "createStudioPanels(",
        "element: studioPanels.properties",
        "element: studioPanels.character",
        "element: studioPanels.adjustments",
        "element: studioPanels.paths",
        "element: studioPanels.patterns",
        "element: studioPanels.history",
    ]:
        assert token in src


def test_studio_panels_css_present():
    src = read("static/css/13-image-editor.css")
    for token in [".ge-panel-body", ".ge-text-align-row", ".ge-adjustment-grid"]:
        assert token in src


def test_studio_panels_module_is_precached():
    src = read("static/sw.js")
    assert "/static/js/editor/panels/studio-panels.js" in src


def test_history_panel_is_a_docked_timeline_not_a_button():
    # Photoshop-style: the docked History panel *is* the clickable state
    # timeline (not a lone "Open History Panel" button), and it shares the
    # row renderer with the floating pop-out window.
    panels = read("static/js/editor/panels/studio-panels.js")
    hist = read("static/js/editor/history-panel.js")
    gallery = read("static/js/galleryEditor.js")
    css = read("static/css/13-image-editor.css")

    assert "Open History Panel" not in panels
    assert "ge-dock-history-list" in panels
    assert "function renderHistoryList(list)" in hist
    assert "document.getElementById('ge-dock-history-list')" in hist
    assert "_refreshHistoryPanelIfOpen();" in gallery
    assert ".ge-history-body" in css
