"""Regression guards for AI document updates while Markdown Preview is visible (#2182)."""

import re
from pathlib import Path
from tests.helpers.document_source import document_source




def _function_body(name: str) -> str:
    text = document_source()
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


def test_markdown_preview_refresh_rerenders_visible_preview():
    body = _function_body("_refreshMarkdownPreviewIfVisible")

    assert "_isMarkdownPreviewVisible()" in body
    assert "lang !== 'markdown'" in body
    assert "textarea.value = content;" in body
    assert "syncHighlighting();" in body
    assert "_setMarkdownPreviewActive(true, { remember: false });" in body


def test_doc_update_refreshes_preview_instead_of_hidden_editor_animation():
    body = _function_body("handleDocUpdate")

    visible = "const markdownPreviewWasVisible = _isMarkdownPreviewVisible();"
    exit_preview = "if (markdownPreviewWasVisible) _setMarkdownPreviewActive(false, { remember: false });"
    diff = "enterDiffMode(oldContent, newContent);"
    refresh = "markdownPreviewWasVisible && _refreshMarkdownPreviewIfVisible(docId, newContent)"
    saved_content = "textarea.value = newContent;"

    assert visible in body
    assert exit_preview not in body
    assert diff not in body
    assert refresh in body
    assert saved_content in body
    assert "_animateDocEdit(textarea, newContent);" not in body
    assert "_refreshMarkdownPreviewIfVisible(docId, newContent);" in body


def test_doc_update_shows_saved_rich_text_without_a_transient_diff():
    body = _function_body("handleDocUpdate")

    assert "const isRichTextUpdate = _isRichTextLang(docLang);" in body
    assert "if (isRichTextUpdate && updatedDocForRichText)" in body
    assert "_showRichTextEditor(updatedDocForRichText);" in body
    assert "_animateRichTextEdit" not in body
