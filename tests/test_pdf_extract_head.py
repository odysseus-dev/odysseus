"""extract_pdf_text must not crash on empty/headerless content.

The head computation indexed ``content.splitlines()[0]`` directly when the
markdown header regex (``<!--...-->\\n#...``) did not match. On empty content
``splitlines()`` returns ``[]``, so the ``[0]`` access raised IndexError and
crashed POST /api/document/{doc_id}/extract-pdf-text. ``_pdf_extract_head``
now guards the access and synthesizes a heading instead.
"""
from routes.document_routes import _pdf_extract_head


def test_empty_content_does_not_crash():
    # The regression: empty string -> splitlines()[0] used to raise IndexError.
    assert _pdf_extract_head("", "My Doc") == "# My Doc\n\n"


def test_empty_content_and_no_title_falls_back_to_pdf():
    assert _pdf_extract_head("", None) == "# PDF\n\n"


def test_matching_header_is_preserved_verbatim():
    content = "<!-- pdf_source: u123 -->\n# Title\n\nold body text"
    head = _pdf_extract_head(content, "ignored")
    assert head == "<!-- pdf_source: u123 -->\n# Title\n\n"


def test_headerless_content_keeps_first_line_then_synthesizes_title():
    content = "some leading line\nmore body"
    assert _pdf_extract_head(content, "Doc") == "some leading line\n\n# Doc\n\n"


def test_none_content_is_treated_as_empty():
    assert _pdf_extract_head(None, "Doc") == "# Doc\n\n"
