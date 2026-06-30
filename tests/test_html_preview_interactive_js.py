"""Regression guards for interactive HTML preview iframe focus + Space guard."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_JS = ROOT / "static/js/document.js"
UI_JS = ROOT / "static/js/ui.js"
PANES_JS = ROOT / "static/js/compare/panes.js"
PREVIEW_JS = ROOT / "static/js/htmlPreview.js"


def _function_body(text: str, name: str) -> str:
    match = re.search(rf"\n\s*(?:export\s+)?(?:async\s+)?function\s+{name}\([^)]*\)\s*\{{", text)
    assert match, f"{name} not found"

    start = match.end()
    depth = 1
    i = start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"{name} body did not close"
    return text[start : i - 1]


def test_html_preview_module_exports_focus_helpers():
    text = PREVIEW_JS.read_text(encoding="utf-8")
    assert "export function activateInteractivePreview" in text
    assert "export function deactivateInteractivePreview" in text
    assert "export function isInteractiveHtmlPreviewActive" in text
    assert "doc-html-preview-active" in text
    assert "data-no-swipe-dismiss" in text


def test_toggle_html_preview_activates_interactive_preview():
    text = DOC_JS.read_text(encoding="utf-8")
    body = _function_body(text, "toggleHtmlPreview")
    assert "activateInteractivePreview(iframe" in body


def test_exit_html_preview_deactivates_interactive_preview():
    text = DOC_JS.read_text(encoding="utf-8")
    body = _function_body(text, "exitHtmlPreview")
    assert "deactivateInteractivePreview(iframe)" in body


def test_doc_html_preview_iframe_has_tabindex():
    text = DOC_JS.read_text(encoding="utf-8")
    assert 'id="doc-html-preview"' in text
    assert "tabindex=\"-1\"" in text


def test_ui_space_handler_skips_interactive_html_preview():
    text = UI_JS.read_text(encoding="utf-8")
    assert "import { isInteractiveHtmlPreviewActive } from './htmlPreview.js';" in text
    assert "if (isInteractiveHtmlPreviewActive()) return;" in text


def test_compare_pane_preview_wires_interactive_helpers():
    text = PANES_JS.read_text(encoding="utf-8")
    assert "import { activateInteractivePreview, deactivateInteractivePreview } from '../htmlPreview.js';" in text
    body = _function_body(text, "togglePanePreview")
    assert "activateInteractivePreview(iframe)" in body
    assert "deactivateInteractivePreview(iframe)" in body
