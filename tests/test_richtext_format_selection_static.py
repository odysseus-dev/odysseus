from pathlib import Path
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]


def test_richtext_toolbar_preserves_selection_before_formatting():
    source = document_source()

    assert "let _savedFormatTextareaSelection = null;" in source
    assert "let _savedFormatRichRange = null;" in source
    assert "toolbar.addEventListener('pointerdown', (e) =>" in source
    assert "_saveFormatSelection(e);" in source
    assert "textarea.selectionStart !== textarea.selectionEnd" in source
    assert "ta.selectionStart = _savedFormatTextareaSelection.start;" in source
    assert "const _selection = window.getSelection?.();" in source
    assert "_richFormatRange = _range.cloneRange();" in source
    assert "_selection.addRange(_richFormatRange.cloneRange());" in source
    assert "collapse the browser range before execCommand runs" in source
