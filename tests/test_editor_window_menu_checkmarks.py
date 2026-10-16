"""The Window dropdown toggles panels and dots the ones currently shown.

Every row is a real toggle (click = show/hide), the menu stays open while
clicking, and a dot marks the panels/dialog/workspace that are active. The
dot state is computed by the composer (galleryEditor.js) and handed to
wire-extra-menus as `syncWindowChecks` + `togglePanel`, so that file owns
no state.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
TOPBAR = (ROOT / "static/js/editor/build/topbar.js").read_text(encoding="utf-8")
WIRE = (ROOT / "static/js/editor/wire-extra-menus.js").read_text(encoding="utf-8")
HISTORY = (ROOT / "static/js/editor/history-panel.js").read_text(encoding="utf-8")
GALLERY = (ROOT / "static/js/galleryEditor.js").read_text(encoding="utf-8")
STYLE = (ROOT / "static/css/13-image-editor.css").read_text(encoding="utf-8")
SW = (ROOT / "static/sw.js").read_text(encoding="utf-8")

WINDOW_ACTIONS = {
    "options", "layers", "properties", "character", "adjustments",
    "swatches", "gradients", "channels", "paths", "patterns", "histogram",
    "info", "navigator", "history", "actions",
    "workspace-essentials", "workspace-photography", "workspace-painting",
    "workspace-graphic", "shortcuts",
}


def test_window_menu_rows_carry_dot_glyph_and_checkbox_role():
    rows = re.findall(r'<button class="dropdown-item-compact ge-window-toggle"[^>]*data-window-action="([^"]+)"', TOPBAR)
    assert set(rows) == WINDOW_ACTIONS
    assert TOPBAR.count('role="menuitemcheckbox"') >= len(rows)
    assert TOPBAR.count('<span class="dropdown-icon ge-window-check">●</span>') >= len(rows)


def test_window_menu_is_a_real_toggle_kept_open():
    # wire-extra-menus stays a state-free delegator
    assert "panelVisibility" not in WIRE
    # every panel row is toggled through the composer-supplied dep
    assert "else if (WINDOW_PANEL_ACTIONS.includes(action)) togglePanel?.(action);" in WIRE
    # menu does not auto-close: keepOpen path refreshes the dots instead
    assert "function bind(btnId, menuId, handler, onOpen, keepOpen)" in WIRE
    assert "if (!keepOpen) menu.hidden = true;" in WIRE
    assert "if (keepOpen) onOpen?.();" in WIRE
    assert "syncWindowChecks?.(document.getElementById('ge-window-menu'))" in WIRE
    assert "syncWindowChecks?.(document.getElementById('ge-window-menu')), true);" in WIRE
    # composer computes the dots and supplies the toggle
    assert "function _syncWindowMenuChecks(menu)" in GALLERY
    assert "panelVisibility.isVisible(action)" in GALLERY
    assert "state.historyPanelEl" in GALLERY
    assert "ge-shortcuts-popover" in GALLERY
    assert "togglePanel: (id) => panelVisibility.toggle(id)," in GALLERY


def test_history_row_stays_toggleable():
    # Window/Edit > History synthesises a click on #ge-history-btn, which is in
    # the editor footer (outside any menu). That click bubbles to the document
    # and would trip the menu's click-away handler, closing the menu so the row
    # could only ever open the panel. Guard the away handler for the duration of
    # the row handler.
    assert "let suppressAway = false;" in WIRE
    assert "suppressAway = true;" in WIRE
    assert "try { handler(item); } finally { suppressAway = false; }" in WIRE
    assert "if (suppressAway) return;" in WIRE
    # The floating panel closes on any outside pointerdown; clicking the menu
    # row must not count as "away" or the row's click would reopen it.
    assert "ev.target.closest('.dropdown')" in HISTORY


def test_window_dot_uses_shared_active_glyph_css():
    assert ".ge-window-toggle .ge-window-check { opacity: 0; }" in STYLE
    assert ".ge-window-toggle.active .ge-window-check { opacity: 1; color: var(--red); }" in STYLE


def test_sw_cache_name_bumped_for_window_dots():
    assert re.search(r"odysseus-v\d+-pr40-editor-precache", SW)
