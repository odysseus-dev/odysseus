from src.agent_tools.document_tools import _strip_pdf_editor_markers


def test_pdf_ai_edit_derivative_strips_pdf_plumbing():
    raw = """<!-- pdf_source upload_id="0123456789abcdef0123456789abcdef.pdf" -->

# Contract

- **Name:** Felix <!-- field=Name type=text -->
- Hello world <!-- annotation id=a1 page=1 x=1 y=2 w=3 h=4 kind=text -->
"""

    assert _strip_pdf_editor_markers(raw) == "# Contract\n\n- **Name:** Felix\n- Hello world"


def test_pdf_ai_edit_derivative_strips_form_source_marker():
    raw = """<!-- pdf_form_source upload_id="0123456789abcdef0123456789abcdef.pdf" fields="12" -->

# Form
"""

    assert _strip_pdf_editor_markers(raw) == "# Form"
