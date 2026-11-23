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


def _set_on_state(doc, xref, on_state):
    # PyMuPDF names every new checkbox's on-state "Yes". Rename it in the
    # widget's appearance dictionaries so several boxes sharing one field
    # name carry distinct options, like an "Included / Excluded" row.
    for key in ("AP/N", "AP/D"):
        kind, value = doc.xref_get_key(xref, key)
        if kind == "xref":
            ap_xref = int(value.split()[0])
            doc.update_object(ap_xref, doc.xref_object(ap_xref).replace("/Yes", f"/{on_state}"))
        elif kind == "dict":
            doc.xref_set_key(xref, key, value.replace("/Yes", f"/{on_state}"))


def test_choice_group_keeps_selection_on_a_later_widget(tmp_path):
    fitz = pytest.importorskip("fitz")
    from src.pdf_forms import extract_fields, fill_fields

    source = tmp_path / "choice.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        xrefs = {}
        for left, option in ((50, "Included"), (110, "Excluded")):
            widget = fitz.Widget()
            widget.field_type = fitz.PDF_WIDGET_TYPE_CHECKBOX
            widget.field_name = "scope"
            widget.rect = fitz.Rect(left, 50, left + 20, 70)
            widget.field_value = False
            page.add_widget(widget)
            xrefs[option] = widget.xref
        for option, xref in xrefs.items():
            _set_on_state(doc, xref, option)
        doc.xref_set_key(xrefs["Excluded"], "V", "/Excluded")
        doc.xref_set_key(xrefs["Excluded"], "AS", "/Excluded")
        doc.save(source)

    fields = extract_fields(str(source))
    assert [(f["name"], f["type"], f["value"]) for f in fields] == [("scope", "choice", "Excluded")]

    markdown = render_form_as_markdown(fields, "upload", "Form")
    values = parse_markdown_to_values(markdown)
    assert values == {"scope": "Excluded"}

    filled = tmp_path / "filled.pdf"
    fill_fields(str(source), str(filled), values)
    with fitz.open(filled) as doc:
        states = [widget.field_value for widget in doc[0].widgets()]
    assert states == ["Off", "Excluded"]
