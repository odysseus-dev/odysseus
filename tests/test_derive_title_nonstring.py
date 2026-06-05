from routes.document_helpers import _derive_title


def test_derive_title_handles_non_string_content():
    # content normally comes from a document text column, but the helper is
    # public and a non-string (None / int) made content.strip() raise
    # AttributeError instead of falling back to a default title.
    assert _derive_title(None) == "Untitled"
    assert _derive_title(123) == "Untitled"


def test_derive_title_still_reads_markdown_heading():
    assert _derive_title("# Heading Title\nbody text") == "Heading Title"
