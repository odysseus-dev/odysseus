"""Regression guards for the Documents editor scrolling UI.

Issues #1501 and #1496 both come from the same surface: the document editor
hid its real textarea scrollbar, and the line-number gutter tried to scroll an
overflow-hidden element. Long wrapped lines add another wrinkle: the textarea
can have more visual rows than logical newline rows, so the gutter rows must
match the textarea's measured row heights. Keep these as static checks because
document.js is browser-coupled and not importable in pytest.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_JS = (ROOT / "static/js/document.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static/style.css").read_text(encoding="utf-8")


def test_document_textarea_scrollbar_is_visible():
    textarea_rule_start = STYLE_CSS.index(".doc-editor-textarea {\n  position: absolute;")
    textarea_rule_end = STYLE_CSS.index(".doc-editor-textarea::placeholder", textarea_rule_start)
    textarea_css = STYLE_CSS[textarea_rule_start:textarea_rule_end]

    assert "overflow-y: scroll;" in textarea_css
    assert "scrollbar-width: thin;" in textarea_css
    assert ".doc-editor-textarea::-webkit-scrollbar { width: 8px; }" in STYLE_CSS
    assert ".doc-editor-textarea::-webkit-scrollbar { display: none; }" not in STYLE_CSS


def test_line_number_gutter_translates_inner_content():
    assert "function _lineNumberContentEl(gutter)" in DOC_JS
    assert "inner.className = 'doc-line-number-content';" in DOC_JS
    assert ".style.transform = `translateY(${-textarea.scrollTop}px)`;" in DOC_JS
    assert "gutter.scrollTop = textarea.scrollTop;" not in DOC_JS
    assert ".doc-line-number-content" in STYLE_CSS


def test_line_number_gutter_accounts_for_wrapped_rows():
    assert "function _measureLineNumberHeights(textarea, lines, textWidth, style)" in DOC_JS
    assert "probe = document.createElement('textarea');" in DOC_JS
    assert "probe.wrap = 'soft';" in DOC_JS
    assert "probe.value = line || ' ';" in DOC_JS
    assert "Math.round(probe.scrollHeight / lineHeight)" in DOC_JS
    assert "row.style.height = `${heights[i]}px`;" in DOC_JS
    assert "label.className = 'doc-line-number-label';" in DOC_JS
    assert "inner.textContent = lines;" not in DOC_JS
    assert ".doc-line-number-row" in STYLE_CSS
    assert ".doc-line-number-label" in STYLE_CSS
    assert ".doc-line-number-measure" in STYLE_CSS
