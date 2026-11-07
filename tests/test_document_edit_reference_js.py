"""Regression guards for document-selection references in chat bubbles."""

from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]
RENDERER = (ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")
DOCUMENT = document_source()
STYLE = app_css()
INDEX = (ROOT / "static/index.html").read_text(encoding="utf-8")
APP = (ROOT / "static/app.js").read_text(encoding="utf-8")


def test_live_compact_document_reference_is_rendered_as_an_interactive_tag():
    assert r"(?:L|lines?)\s*[\d–\-]+" in RENDERER
    assert '<button type="button" class="doc-edit-tag"' in RENDERER
    assert "data-doc-edit-ref" in RENDERER


def test_clicking_document_reference_restores_the_editor_selection():
    assert "querySelectorAll('[data-doc-edit-ref]')" in RENDERER
    assert "restoreSelectionReference" in RENDERER
    assert "export async function restoreSelectionReference" in DOCUMENT
    assert "restoreSelectionReference," in DOCUMENT


def test_document_reference_looks_clickable_and_has_keyboard_focus_feedback():
    rule = STYLE.split(".doc-edit-tag {", 1)[1].split("}", 1)[0]

    assert "cursor: pointer" in rule
    assert "border:" in rule
    assert "display: inline-flex" in rule
    assert "border-radius: 999px" in rule
    assert "line-height: 1" in rule
    assert "font-size: 0.68em" in rule
    assert "padding: 1px 5px" in rule
    assert ".doc-edit-tag:hover" in STYLE
    assert ".doc-edit-tag:focus-visible" in STYLE


def test_document_module_has_one_browser_identity_for_restore_and_chat_send():
    """Different query strings create separate JS module selection stores."""
    assert "/static/js/document.js?v=20260916docctx2" in INDEX
    assert "./js/document.js?v=20260916docctx2" in APP
    assert "document.js?v=20260913dirtysaveicon1" not in INDEX


def test_clearing_a_rich_selection_also_resets_native_selection_stats():
    clear_body = function_body("clearSelection")

    assert "browserSelection.removeAllRanges()" in clear_body
    assert "_scheduleDocumentStats()" in clear_body
