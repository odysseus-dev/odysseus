"""Regression guards for exact inline document-suggestion highlighting."""

from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.document_source import document_source

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE_CSS = app_css()


def test_suggestion_highlight_measures_exact_referenced_range():
    assert "const startPos = _measurePos(mirror, text, idx);" in DOC_JS
    assert "const endPos = _measurePos(mirror, text, idx + findText.length);" in DOC_JS
    assert "endPos.x - startPos.x" in DOC_JS
    assert "querySelectorAll('.doc-suggestion-highlight')" in DOC_JS


def test_suggestion_selects_exact_text_inside_textarea():
    assert "textarea.setSelectionRange(start, end, 'forward');" in DOC_JS
    assert "_selectSuggestionText(textarea, idx, idx + sugg.find.length);" in DOC_JS
    assert "textarea.focus({ preventScroll: true });" in DOC_JS


def test_suggestion_highlight_sits_above_editor_text_layer():
    rule_start = STYLE_CSS.index(".doc-suggestion-highlight {")
    rule_end = STYLE_CSS.index("\n}", rule_start)
    rule = STYLE_CSS[rule_start:rule_end]

    assert "z-index: 3;" in rule
    assert "box-shadow:" in rule


def test_suggestions_load_their_document_before_rendering():
    assert "export async function handleDocSuggestions(data)" in DOC_JS
    assert "if (!docs.has(data.doc_id)) await loadDocument(data.doc_id);" in DOC_JS
    assert "if (data.doc_id && activeDocId !== data.doc_id)" in DOC_JS


def test_suggestions_do_not_replace_the_editor_render_layer():
    handler_start = DOC_JS.index("export async function handleDocSuggestions(data)")
    handler_end = DOC_JS.index("/** Render the current suggestion card", handler_start)
    show_start = DOC_JS.index("function _showCurrentSuggestion()", handler_end)
    show_end = DOC_JS.index("// Build the card", show_start)

    assert "_showInlineDiff(" not in DOC_JS[show_start:show_end]
