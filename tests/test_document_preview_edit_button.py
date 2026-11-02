"""Regression guards for the Markdown preview hover-to-edit control."""

from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.document_source import document_source

ROOT = Path(__file__).resolve().parents[1]
DOC_JS = document_source()
STYLE_CSS = app_css()


def test_preview_installs_hover_edit_button():
    assert "function _installMarkdownPreviewEditButton(preview)" in DOC_JS
    assert "button.className = 'doc-preview-hover-edit';" in DOC_JS
    assert "_installMarkdownPreviewEditButton(preview);" in DOC_JS


def test_preview_edit_button_enters_write_mode_and_focuses_editor():
    assert "_setMarkdownPreviewActive(false, { remember: true });" in DOC_JS
    assert "document.getElementById('doc-editor-textarea')?.focus()" in DOC_JS


def test_preview_edit_button_is_hover_revealed_and_sticky():
    assert ".doc-md-preview:hover .doc-preview-hover-edit" in STYLE_CSS
    assert "position: sticky;" in STYLE_CSS
    assert "pointer-events: none;" in STYLE_CSS
