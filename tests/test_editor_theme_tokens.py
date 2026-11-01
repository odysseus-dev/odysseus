"""Editor controls must use the app's design tokens (theme audit, task 2).

The editor previously mixed native controls with a couple of bespoke ones, so
checkboxes/selects/inputs/scrollbars did not match the rest of the product.
These markers assert the token-based alignment block stays in place.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "css" / "documents-gallery-editor.css").read_text()
SW = (ROOT / "static" / "sw.js").read_text()


def test_checkbox_and_radio_use_the_app_dot():
    assert 'body.ge-editor-open input[type="checkbox"],' in CSS
    assert 'body.ge-editor-open input[type="radio"] {' in CSS
    assert "border-radius: 50%;" in CSS
    assert "border: 1.5px solid var(--border);" in CSS
    assert 'body.ge-editor-open input[type="checkbox"]:checked' in CSS
    assert "background: var(--fg);" in CSS


def test_selects_use_the_select_tokens():
    assert "background: var(--select-bg, var(--bg));" in CSS
    assert "color: var(--select-fg, var(--fg));" in CSS
    assert "background: var(--select-option-bg, var(--panel));" in CSS
    assert "background: var(--select-option-active-bg, var(--panel));" in CSS


def test_inputs_use_the_panel_tokens():
    assert 'body.ge-editor-open input[type="text"],' in CSS
    assert 'body.ge-editor-open textarea {' in CSS
    assert "border: 1px solid var(--border);" in CSS
    assert "outline: 1px solid var(--red);" in CSS


def test_scrollbars_use_the_app_recipe():
    assert "scrollbar-color: var(--red) var(--panel);" in CSS
    assert "background-color: var(--red);" in CSS
    assert "border: 2px solid var(--panel);" in CSS


def test_sw_version_is_current():
    m = re.search(r"CACHE_NAME\s*=\s*'([^']+)'", SW)
    assert m, "CACHE_NAME not found in static/sw.js"
    assert re.fullmatch(r"odysseus-v\d+-pr40-editor-precache", m.group(1))
