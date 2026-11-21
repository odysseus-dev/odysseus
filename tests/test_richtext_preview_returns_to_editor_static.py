from pathlib import Path
from tests.helpers.document_source import document_source, function_body


ROOT = Path(__file__).resolve().parents[1]


def test_richtext_preview_returns_to_contenteditable_editor():
    source = document_source()
    body = function_body("_setMarkdownPreviewActive")

    assert "document.getElementById('doc-language-select')?.value || '';" in source
    assert "_getLanguage()" in body
    assert "if (richMode) {" in body
    assert "wrap.style.display = 'none';" in body
    assert "_syncRichEmptyImport(richEmailBody);" in body
