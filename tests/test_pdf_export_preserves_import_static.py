from pathlib import Path
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]


def test_pdf_backed_documents_do_not_offer_destructive_html_pdf_export():
    source = document_source()

    assert "if (!isForm && (_isDocxLang(lang) || typeof window.print === 'function')) {" in source
    assert "label: _isDocxLang(lang) ? 'Convert to PDF' : 'Print / save PDF'" in source
    assert "destroy the original page layout, images, and form structure" in source
