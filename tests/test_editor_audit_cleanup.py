"""Visual audit clean-up regressions (inpaint popover, tool rail, Quick Edit pill,
compact Text/Shape/Rembg strips, label sizes, dock/layer sizing)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
TOOLBAR = (ROOT / "static/js/editor/build/toolbar.js").read_text(encoding="utf-8")
CONTROLS = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
EDITOR = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")


def test_inpaint_popover_stacks_its_rows_vertically():
    start = CSS.index("#ge-inpaint-section.ge-inpaint-popover {")
    block = CSS[start:CSS.index("}", start)]
    assert "display: flex !important;" in block
    assert "flex-direction: column !important;" in block
    assert "flex-wrap: nowrap !important;" in block
    assert "overflow-x: hidden;" in block
    assert ".ge-inpaint-section.ge-inpaint-popover .ge-inpaint-prompt" in CSS
    prompt = CSS[CSS.index(".ge-inpaint-section.ge-inpaint-popover .ge-inpaint-prompt"):]
    assert "width: 100%;" in prompt[:200]


def test_tool_rail_scroller_keeps_swatches_out_of_the_tool_column():
    assert "scroller.className = 'ge-toolbar-scroll';" in TOOLBAR
    assert "toolbar.appendChild(scroller);" in TOOLBAR
    assert "scroller.appendChild(slotEl);" in TOOLBAR
    assert "toolbar.appendChild(swatches);" in TOOLBAR
    toolbar = CSS[CSS.index(".ge-toolbar {"):]
    toolbar = toolbar[:toolbar.index("}")]
    assert "display: flex;" in toolbar
    assert "flex-direction: column;" in toolbar
    assert "overflow: visible;" in toolbar
    assert ".ge-toolbar-scroll {" in CSS
    # The scroller must stretch its slots to the full rail width; centring
    # shrank every tool button to its icon width (~19px instead of 43px).
    scroller = CSS[CSS.index(".ge-toolbar-scroll {"):]
    scroller = scroller[:scroller.index("}")]
    assert "align-items: stretch;" in scroller
    assert "align-items: center;" not in scroller
    swatches = CSS[CSS.index(".ge-tool-swatches {"):]
    swatches = swatches[:swatches.index("}")]
    assert "position: static;" in swatches
    assert "position: sticky;" not in swatches
    assert "attachScrollAffordance(container.querySelector('.ge-toolbar-scroll'))" in EDITOR


def test_collapsed_quick_edit_pill_does_not_add_canvas_scroll():
    start = CSS.index(".ge-ai-command-collapsed {")
    block = CSS[start:CSS.index("}", start)]
    assert "bottom: 0;" in block
    assert "-16px" not in block


def test_text_and_shape_rare_controls_move_into_a_more_popover():
    assert 'id="ge-text-more"' in CONTROLS
    assert 'class="ge-btn ge-btn-sm ge-more-toggle" id="ge-text-more-toggle"' in CONTROLS
    assert 'id="ge-shape-more"' in CONTROLS
    assert 'id="ge-shape-more-toggle"' in CONTROLS
    assert "ge-tool-more" not in CONTROLS
    assert ".ge-opt-more {" in CSS
    assert "position: absolute;" in CSS[CSS.index(".ge-opt-more {"):CSS.index(".ge-opt-more {") + 260]
    assert "['ge-text-more-toggle', 'ge-text-more']" in EDITOR
    assert "['ge-shape-more-toggle', 'ge-shape-more']" in EDITOR
    # Escape is delegated through the gallery's single capture handler.
    esc = EDITOR[EDITOR.index("window.__galleryEditorHandleEscape = "):]
    esc = esc[:esc.index("return false;")]
    assert "document.querySelector('.ge-opt-more:not([hidden])')" in esc
    assert "openMore.hidden = true;" in esc


def test_tall_strips_fit_on_one_row():
    assert "display: none;" in CSS[CSS.index(".ge-options-bar.ge-options-tall .ge-options-tool"):][:80]
    tall = CSS[CSS.index(".ge-options-bar.ge-options-tall .ge-controls--in-options {"):]
    tall = tall[:tall.index("}")]
    assert "flex: 1 1 auto;" in tall
    assert 'rows="2"' in CONTROLS


def test_small_labels_grow_and_transform_steppers_are_bigger():
    dock_tab = CSS[CSS.index(".ge-dock-tab {"):]
    dock_tab = dock_tab[:dock_tab.index("}")]
    assert "font-size: 10px;" in dock_tab
    spin = CSS[CSS.index(".ge-transform-spin {"):]
    spin = spin[:spin.index("}")]
    assert "height: 20px;" in spin
    spin_btn = CSS[CSS.index("\n.ge-transform-spin button {"):]
    spin_btn = spin_btn[:spin_btn.index("}")]
    assert "width: 14px;" in spin_btn
    assert "font-size: 9px;" in spin_btn
    assert "#ge-transform-section .ge-transform-spin button {" in CSS


def test_right_panel_gives_layers_more_room():
    dock = CSS[CSS.index(".ge-dock {"):]
    dock = dock[:dock.index("}")]
    assert "max-height: 56%;" in dock
    assert ".ge-right-panel--dock > .ge-layers { flex: 1 1 auto; min-height: 240px; }" in CSS
