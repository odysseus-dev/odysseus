"""Marquee options simplify to Free / Fixed ratio, and no duplicate mode row.

The options strip used to render a second copy of the marquee mode buttons
(New / + / - / intersect via `ge-opt-mode` mirrors) that duplicated the real
`ge-wand-mode-btn` buttons relocated into the strip. Those mirrors were
removed, and the "Fixed size" marquee style was dropped (only Free and Fixed
ratio remain).
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = (ROOT / "static/js/editor/build/controls.js").read_text(encoding="utf-8")
OPTIONS = (ROOT / "static/js/editor/build/options-bar.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
DOC_EDITOR_CSS = (ROOT / "static/css/documents-gallery-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")


def test_marquee_style_has_no_fixed_size_option():
    assert 'id="ge-marquee-constraint"' in CONTROLS
    i = CONTROLS.index('id="ge-marquee-constraint"')
    block = CONTROLS[i:i + 240]
    assert '<option value="free">Free</option>' in block
    assert '<option value="ratio">Fixed ratio</option>' in block
    assert 'value="size"' not in block
    assert "Fixed size" not in CONTROLS


def test_marquee_has_no_duplicate_mode_mirror_row():
    assert "marquee: { modes: null, feather: null }," in OPTIONS
    assert "if (src.modes) parts.push(makeModes(src.modes));" in OPTIONS


def test_no_selection_tool_renders_a_mode_mirror_row():
    # The real wand-mode buttons are relocated into the strip, so a mirror row
    # duplicates (and collides with) them for every selection tool.
    block = OPTIONS[OPTIONS.index("const SELECTION_TOOLS = {"):]
    block = block[:block.index("};")]
    assert "modes: '#ge-" not in block
    assert "modes: null" in block


def test_sw_cache_name_bumped():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)


def test_options_body_shrinks_to_keep_controls_left_aligned():
    # An empty mirror body must not stretch and push the relocated controls
    # (and the marquee action row) away from the left edge.
    i = CSS.index(".ge-options-body {")
    block = CSS[i:i + 420]
    assert "flex: 0 1 auto;" in block
    assert "flex: 1 1 auto;" not in block


def test_marquee_action_row_is_nudged_up():
    assert ".ge-controls--in-options .ge-control-row.ge-actions { position: relative; top: -2px; margin-left: auto; }" in CSS


def test_auto_select_checkbox_is_accent_when_checked():
    assert 'class="ge-ctl-check"' in OPTIONS  # the auto-select input uses the shared control
    i = DOC_EDITOR_CSS.index('body.ge-editor-open .ge-opt input[type="checkbox"]:checked,')
    block = DOC_EDITOR_CSS[i:i + 220]
    assert "background: var(--accent, var(--red));" in block
    assert "background: var(--fg);" not in block


def test_wand_live_button_sits_next_to_the_intersect_mode_and_is_nudged_up():
    # Live now sits in the mode row, immediately after the intersect (∩) button.
    i = CONTROLS.index('id="ge-wand-section"')
    j = CONTROLS.index('id="ge-wand-tol-preview"')
    mode_row = CONTROLS[i:j]
    assert mode_row.index('data-wand-mode="intersect"') < mode_row.index('id="ge-wand-live"')
    # the tolerance row no longer carries the Live button
    k = CONTROLS.index('id="ge-wand-tol-preview"')
    tol_row = CONTROLS[k:k + 400]
    assert 'id="ge-wand-live"' not in tol_row
    c = CSS.index(".ge-wand-live-btn {")
    live_block = CSS[c:CSS.index("}", c)]
    assert "top: 0;" in live_block


def test_wand_slider_label_hugs_its_text():
    assert "#ge-wand-section .ge-eraser-row label { flex: 0 0 auto; }" in CSS


def test_wand_action_row_is_nudged_up_like_marquee():
    # The wand action row (eye/clear/invert/erase/...) is raised 2px, matching
    # the marquee row, by dropping its inline top margin from 4px to 2px.
    i = CONTROLS.index('id="ge-wand-section"')
    j = CONTROLS.index('id="ge-sam-section"')
    wand = CONTROLS[i:j]
    assert '<div class="ge-control-row ge-actions" style="margin-top:2px;flex-wrap:wrap;">' in wand
    assert '<div class="ge-control-row ge-actions" style="margin-top:4px;flex-wrap:wrap;">' not in wand


def test_wand_action_row_matches_marquee():
    # Wand and marquee share the same selection actions; the wand row must use
    # the same compact buttons as the marquee row (icon-only clear, 14px icons,
    # same labels/modes) instead of its own older 12px + text variants.
    i = CONTROLS.index('id="ge-wand-section"')
    j = CONTROLS.index('id="ge-sam-section"')
    wand = CONTROLS[i:j]
    assert '<button class="ge-btn ge-btn-sm" id="ge-wand-clear" title="Clear selection" aria-label="Clear selection">' in wand
    assert 'data-wand-mode="add" title="Add to selection (Shift)">+</button>' in wand
    assert 'data-wand-mode="subtract" title="Subtract from selection (Alt)">−</button>' in wand
    for label in ("Invert", "Erase", "Copy Layer", "To Mask", "Quick Mask"):
        assert label in wand
    assert wand.count('<svg width="14" height="14"') >= 6
    assert '<svg width="12" height="12"' not in wand


def test_wand_refine_sliders_are_inline():
    # The wand strip carries its post-selection refine sliders (Feather + Edge
    # stroke) and the tip directly: the old "More" dropdown was removed.
    i = CONTROLS.index('id="ge-wand-section"')
    j = CONTROLS.index('id="ge-sam-section"')
    wand = CONTROLS[i:j]
    assert 'id="ge-wand-more"' not in wand
    assert 'ge-wand-more-wrap' not in wand
    assert 'id="ge-wand-refine-feather"' in wand
    assert 'id="ge-wand-refine-grow"' in wand
    assert wand.index('id="ge-wand-refine-feather"') < wand.index('id="ge-wand-refine-grow"')
    assert 'class="ge-wand-tip"' not in wand


def test_shared_more_menu_css_lets_the_strip_overflow():
    assert ".ge-wand-more-menu {" in CSS
    assert ".ge-wand-more-menu[hidden] { display: none; }" in CSS
    for menu in ("wand", "brush", "lasso", "sam", "quickselect", "objectselect"):
        assert f".ge-options-bar:has(#ge-{menu}-more-menu:not([hidden]))" in CSS


def test_wand_has_no_more_dropdown_wiring():
    wire = (ROOT / "static/js/editor/wire-selection-controls.js").read_text(encoding="utf-8")
    assert "getElementById('ge-wand-more')" not in wire
    assert "getElementById('ge-wand-more-menu')" not in wire
    # the other selection tools keep their shared per-tool More menu
    assert "document.getElementById(`${prefix}-more-menu`)" in wire
    assert "const closeWandMore = () => {" not in wire


def test_lasso_action_row_matches_marquee_and_wand():
    # The lasso strip is unified with marquee/wand: same compact action row
    # (icon-only clear, 14px icons, same labels) plus the visibility eye.
    i = CONTROLS.index('id="ge-lasso-section"')
    j = CONTROLS.index('id="ge-wand-section"')
    lasso = CONTROLS[i:j]
    assert '<button class="ge-btn ge-btn-sm" id="ge-lasso-clear" title="Clear selection" aria-label="Clear selection">' in lasso
    assert 'id="ge-lasso-vis"' in lasso
    for label in ("Invert", "Erase", "Copy Layer", "To Mask", "Quick Mask"):
        assert label in lasso
    assert lasso.count('<svg width="14" height="14"') >= 7
    assert '<svg width="12" height="12"' not in lasso
    assert 'class="ge-control-row ge-actions" style="margin-top:2px;flex-wrap:wrap;"' in lasso


def test_lasso_secondary_controls_are_in_a_more_dropdown():
    i = CONTROLS.index('id="ge-lasso-section"')
    j = CONTROLS.index('id="ge-wand-section"')
    lasso = CONTROLS[i:j]
    assert 'id="ge-lasso-more"' in lasso
    assert 'class="ge-lasso-more-wrap"' in lasso
    assert '<path d="m6 9 6 6 6-6"/>' in lasso
    assert lasso.index('id="ge-lasso-more-menu"') < lasso.index('id="ge-lasso-refine-feather"')
    assert lasso.index('id="ge-lasso-mag-contrast') < lasso.index('ge-lasso-tip')
    # ids other tests rely on stay put
    for keep in ('id="ge-lasso-magnetic"', 'id="ge-lasso-mag-width"', 'id="ge-lasso-mag-contrast"'):
        assert keep in lasso


def test_lasso_more_dropdown_css_and_wire():
    assert ".ge-lasso-more-menu {" in CSS
    assert ".ge-lasso-more-menu[hidden] { display: none; }" in CSS
    assert ".ge-options-bar:has(#ge-lasso-more-menu:not([hidden])) { overflow: visible; }" in CSS
    wire = (ROOT / "static/js/editor/wire-selection-controls.js").read_text(encoding="utf-8")
    assert "const lassoMore = document.getElementById('ge-lasso-more');" in wire
    assert "const closeLassoMore = () => {" in wire
    assert "document.getElementById('ge-lasso-clear')?.addEventListener('click', wandClear);" in wire
    assert "'ge-lasso-vis'" in wire


def test_lasso_clear_clears_the_selection_mask():
    # The toolbar clear badges (data-clear-tool="lasso"/"quickselect"/
    # "objectselect") route through the same _wandClear path as marquee/wand,
    # so they clear the materialized selection.
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    assert ("if (which === 'marquee' || which === 'wand' || which === 'lasso'\n"
            "        || which === 'quickselect' || which === 'objectselect') {") in editor
    assert "#ge-marquee-clear, #ge-wand-clear, #ge-lasso-clear" in editor



def _selection_section(tool):
    import re as _re
    m = _re.search(rf'id="ge-{tool}-section"[\s\S]*?\n    </div>', CONTROLS)
    assert m, f"section ge-{tool}-section not found"
    return m.group(0)


def test_quickselect_objectselect_sam_match_the_wand_strip():
    # The three remaining selection tools were refactored to the wand shape:
    # compact mode labels, a caret "More" menu (help moved into it), and the
    # unified 7-button action row.
    for tool in ("sam", "quickselect", "objectselect"):
        sec = _selection_section(tool)
        assert 'class="ge-wand-more-wrap"' in sec
        assert f'id="ge-{tool}-more"' in sec
        assert f'id="ge-{tool}-more-menu"' in sec
        assert 'aria-haspopup="true"' in sec
        assert 'class="ge-wand-tip"' in sec
        # compact mode labels (no verbose "+ Add" / "− Subtract")
        assert "+ Add</button>" not in sec
        assert "− Subtract</button>" not in sec
        assert 'data-wand-mode="add" title="Add to selection">+</button>' in sec
        assert 'data-wand-mode="subtract" title="Subtract from selection">−</button>' in sec
        # the unified action row
        assert '<div class="ge-control-row ge-actions" style="margin-top:2px;flex-wrap:wrap;">' in sec
        assert f'id="ge-{tool}-clear" title="Clear selection" aria-label="Clear selection"' in sec
        for action in ("vis", "clear", "invert", "delete", "copy", "mask"):
            assert f'id="ge-{tool}-{action}"' in sec
        for label in ("Invert", "Erase", "Copy Layer", "To Mask", "Quick Mask"):
            assert label in sec
        assert sec.count('<svg width="14" height="14"') >= 7


def test_selection_tool_carets_reuse_the_wand_classes():
    # CSS: the new menus reuse the wand caret classes and un-clip the strip.
    for tool in ("sam", "quickselect", "objectselect"):
        assert f".ge-options-bar:has(#ge-{tool}-more-menu:not([hidden])) {{ overflow: visible; }}" in CSS
    # the clear ✕ nudge + active-accent rules cover the new buttons too
    for tool in ("sam", "quickselect", "objectselect"):
        assert f"#ge-{tool}-clear svg" in CSS
        assert f"#ge-{tool}-clear.has-selection" in CSS


def test_selection_tool_action_wiring_is_shared():
    wire = (ROOT / "static/js/editor/wire-selection-controls.js").read_text(encoding="utf-8")
    assert "const wireSelectionCaret = (prefix) => {" in wire
    assert "['ge-sam', 'ge-quickselect', 'ge-objectselect'].forEach((prefix) => {" in wire
    assert "document.getElementById(`${prefix}-vis`)?.addEventListener('click', toggleSelectionOverlay);" in wire
    assert "document.getElementById(`${prefix}-clear`)?.addEventListener('click', wandClear);" in wire
    assert "document.getElementById(`${prefix}-delete`)?.addEventListener('click', wandDeleteSelection);" in wire
    assert "document.getElementById(`${prefix}-mask`)?.addEventListener('click', wandToMask);" in wire
    assert "wireSelectionCaret(prefix);" in wire
    # the old one-off SAM bindings were removed (no double toggle / double bind)
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    assert "document.getElementById('ge-sam-vis')?.addEventListener('click', () => {" not in editor
    assert "document.getElementById('ge-sam-clear')?.addEventListener('click', () => _wandClear());" not in editor
    # the clear indicator lights the new buttons too
    assert "#ge-marquee-clear, #ge-wand-clear, #ge-lasso-clear, #ge-sam-clear, #ge-quickselect-clear, #ge-objectselect-clear" in editor


def test_quickselect_brush_size_slider_is_wired():
    wire = (ROOT / "static/js/editor/wire-selection-controls.js").read_text(encoding="utf-8")
    assert "state.quickSelectBrushSize = Math.max(4, Math.min(200, parseInt(quickSize.value, 10) || 24));" in wire


def test_quickselect_brush_size_uses_the_shared_eraser_row_slider_style():
    # Quick Select's brush-size slider kept the bare .ge-ctl-range look (14px,
    # transparent) instead of the shared .ge-eraser-row slider the wand
    # Tolerance row uses. Its row now uses the same pattern (8px track, preview
    # dot, value chip) so every tool's slider reads the same.
    q = _selection_section("quickselect")
    assert 'class="ge-control-row ge-eraser-row"' in q
    assert 'id="ge-quickselect-size-preview"' in q
    assert 'id="ge-quickselect-size-label"' in q
    assert 'class="ge-quickselect-size" id="ge-quickselect-size"' in q
    assert "#ge-quickselect-section .ge-eraser-row label { flex: 0 0 auto; }" in CSS
    wire = (ROOT / "static/js/editor/wire-selection-controls.js").read_text(encoding="utf-8")
    assert "document.getElementById('ge-quickselect-size-label')" in wire
