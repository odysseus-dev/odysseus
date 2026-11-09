"""Editor controls must use the app's design tokens (theme audit, part 2).

Extends tests/test_editor_theme_tokens.py: buttons, segmented toggles, tabs,
list rows, panel headers, badges, focus rings and colour-picker chrome are
mapped onto the shared tokens (existing tokens only, no new colours). These
markers assert the second alignment block stays in place.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "documents-gallery-editor.css").read_text()


def test_muted_aliases_map_to_app_tokens():
    assert "--fg-muted: var(--color-muted);" in CSS
    assert "--bg-secondary: var(--panel);" in CSS


def test_buttons_use_the_app_recipe():
    assert "body.ge-editor-open .ge-btn {" in CSS
    assert "border-radius: 5px;" in CSS
    assert "body.ge-editor-open .ge-btn-primary {" in CSS
    assert "color: var(--bg);" in CSS
    assert "body.ge-editor-open .ge-win-close:hover {" in CSS


def test_segmented_toggles_use_the_accent_token():
    assert "body.ge-editor-open .ge-opt-mode.active," in CSS
    assert "body.ge-editor-open .ge-crop-preset.active," in CSS
    assert "body.ge-editor-open .ge-command-item.active," in CSS
    assert "background: var(--accent, var(--red));" in CSS


def test_tab_and_swatch_states_use_the_accent_token():
    assert "body.ge-editor-open .ge-dock-tab.active {" in CSS
    assert "border-bottom-color: var(--accent, var(--red));" in CSS
    assert "body.ge-editor-open .ge-swatch.active {" in CSS
    assert "outline-color: var(--accent, var(--red));" in CSS


def test_panel_headers_and_badges_use_app_tokens():
    assert "body.ge-editor-open .ge-panel-title," in CSS
    assert "color: var(--color-muted);" in CSS
    assert "body.ge-editor-open .ge-tool-clear {" in CSS


def test_focus_rings_use_the_app_recipe():
    assert "body.ge-editor-open .ge-btn:focus-visible," in CSS
    assert "body.ge-editor-open .ge-history-row:focus-visible," in CSS
    assert "body.ge-editor-open .ge-stepper button:focus-visible {" in CSS
    assert "outline-offset: 1px;" in CSS
