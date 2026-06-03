"""Regression test: the '[PDF content]:' wrapper must be removed without eating
into the page text that follows it.

The old call sites used ``str.lstrip("\\n[PDF content]:")``, which treats the
argument as a *set of characters* and keeps stripping leading characters that
happen to be in that set — corrupting the start of the extracted document.
"""
from src.document_processor import strip_pdf_content_marker, _PDF_CONTENT_MARKER


def test_marker_removed_without_eating_following_text():
    # Shape that _process_pdf actually returns: marker + "\n\n[Page 1 text]:" + body.
    raw = "\n\n[PDF content]:\n\n[Page 1 text]:\nto the board, content begins"
    out = strip_pdf_content_marker(raw)
    assert out == "[Page 1 text]:\nto the board, content begins"
    # The old lstrip approach produced "age 1 text]:..." (ate "[P" then "to").
    assert not out.startswith("age 1 text")


def test_marker_constant_matches_processor_output():
    # If _process_pdf's prefix ever changes, this guards the consumer.
    assert _PDF_CONTENT_MARKER == "\n\n[PDF content]:"


def test_text_without_marker_is_only_stripped():
    assert strip_pdf_content_marker("  plain text  ") == "plain text"


def test_handles_none():
    assert strip_pdf_content_marker(None) == ""
