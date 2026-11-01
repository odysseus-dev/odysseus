from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


def test_select_menu_has_a_modify_submenu_with_the_five_photoshop_items():
    topbar = _read("static/js/editor/build/topbar.js")
    # A Modify parent submenu...
    assert 'class="dropdown-item-compact ge-menu-parent" data-submenu="selection-modify"' in topbar
    assert '<div class="ge-submenu" data-submenu-panel="selection-modify">' in topbar
    # ...with Border / Smooth / Expand / Contract / Feather in Photoshop order.
    order = [
        ('modifyBorder', 'Border'),
        ('modifySmooth', 'Smooth'),
        ('modifyExpand', 'Expand'),
        ('modifyContract', 'Contract'),
        ('modifyFeather', 'Feather'),
    ]
    positions = []
    for action, label in order:
        needle = f'data-selection-action="{action}"><span>{label}…</span>'
        assert needle in topbar, needle
        positions.append(topbar.index(needle))
    assert positions == sorted(positions)


def test_modify_items_are_wired_to_control_ops_and_disabled_without_a_selection():
    menus = _read("static/js/editor/wire-topbar-menus.js")
    # A numeric prompt helper drives every Modify item.
    assert "function promptNumber(message, fallback, run) {" in menus
    assert "const raw = window.prompt(message, String(fallback));" in menus
    assert "if (Number.isFinite(value)) run(value);" in menus
    # All five are disabled when there is no live selection.
    assert "for (const id of ['modifyBorder', 'modifySmooth', 'modifyExpand', 'modifyContract', 'modifyFeather']) {" in menus
    assert 'menu.querySelector(`[data-selection-action="${id}"]`)?.toggleAttribute(\'disabled\', !hasSelection);' in menus
    # The submenu needs its open/close toggle wired in the Select menu (it was
    # previously only wired for Image/Filter).
    assert "wireSubmenuToggles(menu);" in menus
    # Each item dispatches to the matching control op with the right parameter.
    assert "else if (action === 'modifyFeather') { promptNumber('Feather radius (px)', 8, v => window.geEditorControl?.invoke?.('selection.feather', { radius: v })); handled = true; }" in menus
    assert "else if (action === 'modifyExpand') { promptNumber('Expand by (px)', 4, v => window.geEditorControl?.invoke?.('selection.expand', { amount: v })); handled = true; }" in menus
    assert "else if (action === 'modifyContract') { promptNumber('Contract by (px)', 4, v => window.geEditorControl?.invoke?.('selection.contract', { amount: v })); handled = true; }" in menus
    assert "else if (action === 'modifyBorder') { promptNumber('Border width (px)', 4, v => window.geEditorControl?.invoke?.('selection.border', { width: v })); handled = true; }" in menus
    assert "else if (action === 'modifySmooth') { promptNumber('Smooth radius (px)', 4, v => window.geEditorControl?.invoke?.('selection.smooth', { radius: v })); handled = true; }" in menus


def test_border_op_builds_an_edge_band_by_subtracting_an_eroded_selection():
    editor = _read("static/js/galleryEditor.js")
    start = editor.index("'selection.border':")
    op = editor[start:editor.index("'selection.smooth':", start)]

    assert "if (!state.wandMask) throw new Error('no selection');" in op
    assert "const wdt = Math.max(1, Math.min(64, Number(p.width) || 4));" in op
    # Outer (dilated) minus inner (eroded) = a band hugging the original edge.
    assert "const outer = _growMaskCanvas(state.wandMask, wdt, true);" in op
    assert "const inner = _growMaskCanvas(state.wandMask, wdt, false);" in op
    assert "octx.globalCompositeOperation = 'destination-out';" in op
    assert "state.wandMask = out;" in op
    assert "return { width: wdt };" in op


def test_smooth_op_rounds_corners_then_re_binarises_the_selection():
    editor = _read("static/js/galleryEditor.js")
    start = editor.index("'selection.smooth':")
    op = editor[start:editor.index("'selection.grow':", start)]

    assert "if (!state.wandMask) throw new Error('no selection');" in op
    assert "const r = Math.max(1, Math.min(64, Number(p.radius) || 4));" in op
    assert "_featherMaskCanvas(" in op
    # Feather softens, then the alpha is re-thresholded so the selection stays
    # a hard mask (matching Photoshop's Smooth rather than a feathered edge).
    assert "id.data[i] = id.data[i] >= 128 ? 255 : 0;" in op
    assert "state.wandMask = sm;" in op
    assert "return { radius: r };" in op


def test_the_pre_existing_modify_ops_are_still_available():
    editor = _read("static/js/galleryEditor.js")
    for op in ("'selection.feather'", "'selection.expand'", "'selection.contract'", "'selection.grow'", "'selection.similar'"):
        assert op in editor
