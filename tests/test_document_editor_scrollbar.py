from pathlib import Path


CSS = Path("static/style.css").read_text(encoding="utf-8")


def test_document_editor_keeps_a_visible_scrollbar_for_long_docs():
    assert "scrollbar-gutter: stable;" in CSS
    assert "scrollbar-width: thin;" in CSS
    assert ".doc-editor-textarea::-webkit-scrollbar { display: none; }" not in CSS
    assert ".doc-editor-textarea::-webkit-scrollbar {\n  width: 10px;\n}" in CSS
