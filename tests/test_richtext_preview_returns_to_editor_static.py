from pathlib import Path
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]


def test_richtext_preview_returns_to_contenteditable_editor():
    source = document_source()

    assert "const currentLang = document.getElementById('doc-language-select')?.value || '';" in source
    assert "if (richMode) {" in source
    assert "wrap.style.display = 'none';" in source
    assert "_syncRichEmptyImport(richEmailBody);" in source
