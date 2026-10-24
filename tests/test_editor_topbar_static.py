from pathlib import Path
import re
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
OVERFLOW = (ROOT / "static/js/editor/wire-topbar-overflow.js").read_text(encoding="utf-8")
STYLE = app_css()


def test_primary_editor_text_actions_use_stacked_toolbar_contract():
    for control_id in (
        "ge-view-menu-btn",
        "ge-image-menu-btn",
        "ge-selection-menu-btn",
        "ge-filter-menu-btn",
        "ge-import-topbar",
        "ge-save-menu-btn",
    ):
        marker = f'id="{control_id}"'
        start = TOPBAR.rfind("<button", 0, TOPBAR.index(marker))
        end = TOPBAR.index("</button>", TOPBAR.index(marker))
        button = TOPBAR[start:end]
        assert "ge-stacked-btn" in button
        assert "ge-stacked-glyph" in button
        assert "ge-stacked-label" in button


def test_stacked_labels_share_one_size_and_position_rule():
    assert ".ge-stacked-btn .ge-stacked-label" in STYLE
    assert "font-size: 8px;" in STYLE
    assert "top: 2px;" in STYLE


def test_narrow_topbar_scrolls_essential_actions_and_hides_ai_group_first():
    assert "if (topbar.scrollWidth > topbar.clientWidth)" in OVERFLOW
    assert "aiGroup.forEach(el => { el.style.display = 'none'; });" in OVERFLOW
    assert "new ResizeObserver(() => syncOverflow())" in OVERFLOW
    mobile = STYLE[STYLE.index("/* Editor topbar — scrolls horizontally") :]
    topbar = mobile[mobile.index(".ge-topbar {") : mobile.index("}", mobile.index(".ge-topbar {"))]
    assert "overflow-x: auto;" in topbar
    assert "flex-wrap: nowrap;" in topbar
    assert "justify-content: flex-start;" in topbar


def test_mobile_topbar_pins_the_right_actions():
    # On phones the menu row scrolls internally so Import/Save stay on-screen
    # instead of the whole topbar scrolling them off the right edge.
    assert ".ge-topbar > .ge-topbar-left {" in STYLE
    assert ".ge-topbar > .ge-topbar-right { flex: 0 0 auto; margin-left: 0; }" in STYLE
    block = STYLE[STYLE.index(".ge-topbar > .ge-topbar-left {") :]
    block = block[: block.index("}")]
    assert "flex: 1 1 0;" in block
    assert "min-width: 0;" in block
    assert "overflow-x: auto;" in block


def test_desktop_tool_rail_keeps_long_tool_names_readable():
    toolbar_start = STYLE.index(".ge-toolbar {")
    toolbar_end = STYLE.index(".ge-toolbar::-webkit-scrollbar", toolbar_start)
    toolbar = STYLE[toolbar_start:toolbar_end]
    assert "width: 50px;" in toolbar
    label_rule = re.search(r"(?m)^\.ge-tool-label\s*\{([^}]*)\}", STYLE[toolbar_end:])
    assert label_rule
    assert "font-size: 9px;" in label_rule.group(1)


def test_mobile_footer_hides_wide_readouts():
    # Audit 2026-10-11 #1: the footer overflowed horizontally on phones so the
    # zoom controls sat off-screen; drop the wide text readouts on <=700px.
    assert ".ge-editor-footer .ge-status-tool,\n  .ge-editor-footer .ge-status-model,\n  .ge-editor-footer .ge-canvas-size {" in STYLE
    block_start = STYLE.index("@media (max-width: 700px) {\n  .ge-editor-footer {")
    block = STYLE[block_start:]
    block = block[: block.index("\n}\n", block.index(".ge-footer-history > .ge-btn"))]
    assert "overflow: hidden;" in block
    assert "min-width: 0;" in block


def test_mobile_toast_avoids_editor_top_rows():
    # Audit 2026-10-11 #7: a shared toast at the viewport top overlapped the
    # editor menu row on phones. EVAL (d5ec9a3) moved it to the bottom-centre
    # while the editor is open, so it clears both editor top rows AND any
    # top-docked popup (Develop/Filters).
    assert "body.ge-editor-open .toast.toast-editor-bottom {" in STYLE
    assert "body.ge-editor-open .toast {" in STYLE
    assert "bottom: 84px;" in STYLE
    editor = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
    assert "document.body.classList.add('ge-editor-open')" in editor
    assert "document.body.classList.remove('ge-editor-open')" in editor
