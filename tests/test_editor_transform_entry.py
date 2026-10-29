from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_entering_transform_from_a_shortcut_swaps_the_options_strip():
    # A shortcut must behave like picking the Transform tool: the previous
    # tool's options (e.g. Text) are hidden so they don't overlap the
    # transform controls.
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    assert "const _enterTransform = () => {" in editor
    assert "toolbar.querySelector('.ge-tool-flyout-item[data-tool-option=\"transform\"]')" in editor
    assert "startTransform: _enterTransform," in editor


def test_transform_entry_shows_a_one_time_hint():
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    assert "const _maybeShowTransformHint = () => {" in editor
    assert "sessionStorage.getItem('ge-transform-hint')" in editor
    assert "sessionStorage.setItem('ge-transform-hint', '1');" in editor
    assert "Free Transform \u2014 Enter applies, Esc cancels. Shortcut: Ctrl+Alt+T" in editor
    assert "_startTransform();\n        _maybeShowTransformHint();" in editor


def test_shortcuts_popover_lists_ctrl_alt_t_for_transform():
    popups = (ROOT / "static/js/editor/build/popups.js").read_text(encoding="utf-8")
    assert "<kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>T</kbd> Free Transform</div>" in popups


def test_transform_hint_is_pinned_to_one_stable_spot():
    # The hint used to jump between the top-right corner and the full-width
    # bottom banner depending on what else was open.
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    assert "{ toastClass: 'toast-transform-hint' }" in editor
    ui = (ROOT / "static/js/ui.js").read_text(encoding="utf-8")
    assert "if (el.classList.contains('toast-transform-hint')) {" in ui
    assert "'toast-message-sent', 'toast-transform-hint');" in ui
    css = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
    block = css[css.index("body.ge-editor-open .toast.toast-transform-hint {"):]
    block = block[:block.index("}")]
    assert "bottom: 84px;" in block
    assert "left: 0;" in block and "right: 0;" in block
    assert "margin-left: auto;" in block and "margin-right: auto;" in block


def test_transform_preserve_label_stays_on_one_line():
    css = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
    block = css[css.index("#ge-transform-section .ge-transform-preserve-field {"):]
    block = block[:block.index("}")]
    assert "flex: 0 0 auto;" in block
    assert "white-space: nowrap;" in block
    assert "#ge-transform-section .ge-transform-preserve-field span { display: none; }" in css
    popup = (ROOT / "static/js/editor/build/transform-popup.js").read_text(encoding="utf-8")
    assert 'title="Preserve source pixels (applies the transform non-destructively)"' in popup
    assert 'aria-label="Preserve source pixels"' in popup
