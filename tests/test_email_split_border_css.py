from pathlib import Path

from tests.helpers.css_loader import load_runtime_css_text

CSS = load_runtime_css_text()


def _rule(selector: str) -> str:
    return CSS.split(selector, 1)[1].split("}", 1)[0]


def test_email_split_document_pane_drops_duplicate_border():
    rule = _rule("body.email-doc-split-active.doc-view .doc-editor-pane {")
    assert "border-left: none !important;" in rule


def test_email_split_panel_keeps_visible_seam():
    rule = _rule(".modal.email-snap-left .modal-content {")
    assert "border-right: 1px solid var(--border);" in rule
