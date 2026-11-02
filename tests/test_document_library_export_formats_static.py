from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_document_library_export_submenu_honors_markdown_and_text_formats():
    source = (ROOT / "static/js/documentLibrary.js").read_text(encoding="utf-8")

    assert "function _documentExport(doc, extMap, format = 'original')" in source
    assert "const exportDocumentFile = async (format = 'original')" in source
    assert "_documentExport(full, extMap, format)" in source
    assert "exportDocumentFile('markdown')" in source
    assert "exportDocumentFile('text')" in source
