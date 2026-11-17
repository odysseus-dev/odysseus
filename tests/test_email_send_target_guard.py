from pathlib import Path
from tests.helpers.document_source import document_source


SCRIPT = document_source()


def test_email_send_requires_actual_send_button_event_target():
    assert "target.closest('#doc-email-send-btn')" in SCRIPT
    assert "const btn = targetBtn || null;" in SCRIPT
    assert "rectBtn" not in SCRIPT
    assert "_eventInsideElement" not in SCRIPT


def test_email_caret_requires_actual_caret_event_target():
    assert "target.closest('#doc-email-send-caret')" in SCRIPT
    assert "const caret = targetCaret || null;" in SCRIPT
    assert "rectCaret" not in SCRIPT
