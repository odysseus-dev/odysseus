"""Regression guards for document-card direct export."""

from pathlib import Path


DOC_LIBRARY_JS = (
    Path(__file__).resolve().parents[1] / "static/js/documentLibrary.js"
).read_text(encoding="utf-8")


def test_expanded_document_card_has_export_beside_clone():
    export = DOC_LIBRARY_JS.index("btnRow.appendChild(expandedExportBtn);")
    clone = DOC_LIBRARY_JS.index("btnRow.appendChild(cloneBtn);", export)
    opened = DOC_LIBRARY_JS.index("btnRow.appendChild(openBtn);", clone)

    assert export < clone < opened
    assert "expandedExportBtn.title = 'Export — save to this computer';" in DOC_LIBRARY_JS


def test_expanded_export_reuses_download_function_without_proxy_click():
    assert "const exportDocumentFile = async (format = 'original') =>" in DOC_LIBRARY_JS
    assert "await exportDocumentFile();" in DOC_LIBRARY_JS
    assert "exportItem.click();" not in DOC_LIBRARY_JS
    assert "exportItem.type = 'button';" in DOC_LIBRARY_JS
    assert "expandedExportBtn.type = 'button';" in DOC_LIBRARY_JS
    assert "expandedExportBtn.addEventListener('click', async (e) => {\n      e.preventDefault();" in DOC_LIBRARY_JS
