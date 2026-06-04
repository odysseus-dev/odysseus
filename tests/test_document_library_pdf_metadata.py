from types import SimpleNamespace

from routes.document_routes import _aggregate_language_facets, _library_language_for_document


def test_pdf_backed_plain_document_displays_as_pdf_in_library():
    doc = SimpleNamespace(
        language="markdown",
        current_content='<!-- pdf_source upload_id="0123456789abcdef0123456789abcdef.pdf" -->\n\n# Packet\n',
    )

    assert _library_language_for_document(doc) == "pdf"


def test_pdf_backed_form_document_displays_as_pdf_in_library():
    doc = SimpleNamespace(
        language="markdown",
        current_content=(
            '<!-- pdf_form_source upload_id="0123456789abcdef0123456789abcdef.pdf" fields="3" -->'
            "\n\n# Intake Form\n"
        ),
    )

    assert _library_language_for_document(doc) == "pdf"


def test_non_pdf_library_language_is_unchanged():
    assert _library_language_for_document(
        SimpleNamespace(language="python", current_content="print('ok')\n")
    ) == "python"
    assert _library_language_for_document(
        SimpleNamespace(language=None, current_content="plain text")
    ) == "text"


def test_pdf_language_facet_counts_are_summed():
    rows = [("pdf", 1), ("markdown", 2), ("pdf", 1), (None, 1)]

    assert _aggregate_language_facets(rows) == {
        "pdf": 2,
        "markdown": 2,
        "text": 1,
    }
