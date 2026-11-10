import pytest

from src.pdf_form_doc import parse_markdown_to_values, render_form_as_markdown


def test_unchecked_checkbox_stays_unchecked_through_import_and_export(tmp_path):
    fitz = pytest.importorskip("fitz")
    from src.pdf_forms import extract_fields, fill_fields

    source = tmp_path / "form.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        for top, (name, checked) in ((50, ("agree", False)), (90, ("subscribe", True))):
            widget = fitz.Widget()
            widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
            widget.field_name = name
            widget.rect = fitz.Rect(50, top, 70, top + 20)
            widget.field_value = checked
            page.add_widget(widget)
        doc.save(source)

    markdown = render_form_as_markdown(extract_fields(str(source)), "upload", "Form")
    assert "- [ ] **agree** <!-- field=agree type=checkbox -->" in markdown
    assert "- [x] **subscribe** <!-- field=subscribe type=checkbox -->" in markdown

    filled = tmp_path / "filled.pdf"
    fill_fields(str(source), str(filled), parse_markdown_to_values(markdown))
    with fitz.open(filled) as doc:
        states = {widget.field_name: widget.field_value for widget in doc[0].widgets()}
    assert states == {"agree": "Off", "subscribe": "Yes"}
