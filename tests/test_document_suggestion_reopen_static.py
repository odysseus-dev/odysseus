from pathlib import Path
from tests.helpers.document_source import document_source


SOURCE = document_source()


def test_newly_mounted_document_pane_cannot_save_before_binding():
    assert "mountedPane?.dataset.activeDocumentId === activeDocId" in SOURCE
    assert "mountedPane.dataset.activeDocumentId = docId" in SOURCE


def test_suggestion_event_rebinds_a_fresh_editor_shell():
    assert "const openedPanel = !isOpen;" in SOURCE
    assert "data.doc_id && openedPanel && docs.has(data.doc_id)" in SOURCE
    assert "switchToDoc(data.doc_id);" in SOURCE
