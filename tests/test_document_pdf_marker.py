"""Regression test: the '[PDF content]:' wrapper must be removed without eating
into the page text that follows it.

The old call sites used ``str.lstrip("\\n[PDF content]:")``, which treats the
argument as a *set of characters* and keeps stripping leading characters that
happen to be in that set — corrupting the start of the extracted document.
"""
from src import document_processor
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


def test_local_document_extraction_disables_embedded_image_analysis(monkeypatch, tmp_path):
    source = tmp_path / "brief.pdf"
    source.write_bytes(b"%PDF-1.4")
    observed = {}

    def fake_process(path, owner=None, *, analyze_embedded_images=True):
        observed.update(
            path=path,
            owner=owner,
            analyze_embedded_images=analyze_embedded_images,
        )
        return "extracted"

    monkeypatch.setattr(document_processor, "_process_pdf", fake_process)

    result = document_processor.extract_local_document(str(source))

    assert result == "extracted"
    assert observed == {
        "path": str(source),
        "owner": None,
        "analyze_embedded_images": False,
    }
