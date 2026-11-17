"""Editor controls must use the app's design tokens (theme audit, part 3).

Extends tests/test_editor_theme_tokens.py and test_editor_theme_tokens_more.py:
the setup / preflight checklist, the options-bar checkbox and the remaining
list-row / badge chrome are mapped onto the shared tokens (existing tokens only,
no new colours). These markers assert the third alignment block stays in place.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "documents-gallery-editor.css").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def test_preflight_card_and_rows_use_app_tokens():
    assert "body.ge-editor-open .ge-preflight-card {" in CSS
    assert "body.ge-editor-open .ge-preflight-row {" in CSS
    assert "background: var(--panel);" in CSS
    assert "border-color: var(--border);" in CSS


def test_preflight_badges_use_semantic_tokens():
    assert "body.ge-editor-open .ge-preflight-row.is-done .ge-preflight-badge {" in CSS
    assert "body.ge-editor-open .ge-preflight-row.is-todo {" in CSS
    assert "body.ge-editor-open .ge-preflight-row.is-optional {" in CSS
    assert "color: var(--green);" in CSS
    assert "color: var(--red);" in CSS


def test_preflight_copy_and_actions_use_muted_and_accent_tokens():
    assert "body.ge-editor-open .ge-preflight-hint," in CSS
    assert "body.ge-editor-open .ge-preflight-act {" in CSS
    assert "body.ge-editor-open .ge-preflight-start {" in CSS
    assert "color: var(--color-muted);" in CSS
    assert "background: var(--accent, var(--red));" in CSS
    assert "color: var(--bg);" in CSS


def test_options_bar_checkbox_matches_the_app_dot():
    assert 'body.ge-editor-open .ge-opt input[type="checkbox"],' in CSS
    assert 'body.ge-editor-open .ge-opt input[type="checkbox"]:checked::after {' in CSS
    assert "content: none;" in CSS
    assert "border-radius: 50%;" in CSS


def test_list_rows_use_panel_border_and_muted_tokens():
    assert "body.ge-editor-open .ge-history-row-dot {" in CSS
    assert "background: var(--color-muted);" in CSS
    assert "body.ge-editor-open .ge-layer-inline-thumb {" in CSS
    assert "body.ge-editor-open .ge-channel-eye {" in CSS


def test_sw_version_is_current():
    m = re.search(r"CACHE_NAME\s*=\s*'([^']+)'", SW)
    assert m, "CACHE_NAME not found in static/sw.js"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
