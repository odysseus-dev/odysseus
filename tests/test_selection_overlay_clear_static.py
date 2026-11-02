from pathlib import Path
from tests.helpers.stylesheets import app_css
from tests.helpers.document_source import document_source


ROOT = Path(__file__).resolve().parents[1]


def test_selection_overlays_have_individual_clear_controls():
    js = document_source()
    css = app_css()

    assert "function clearSelectionAt(index)" in js
    assert "className = 'doc-selection-overlay-clear'" in js
    assert "clearSelectionAt(selectionIndex);" in js
    assert "const removed = _selections[index];" in js
    assert "browserSelection.removeAllRanges();" in js
    assert "Delete the persistent CSS highlight before checking whether any" in js
    assert "doc-selection-rich-clear" in js
    assert ".doc-selection-overlay-clear" in css
    assert ".doc-selection-rich-clear" in css
    assert "pointer-events: auto;" in css
