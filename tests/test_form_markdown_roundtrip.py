"""Regression: PDF-form markdown export must not drop values whose label
contains an asterisk.

`parse_markdown_to_values` is the read-back path for GET .../export-pdf, the
export preview, and prepare-signed-reply. Its bullet regexes matched the bold
label with `[^*]+`, so they could not match a label like "Email *" / "State *"
/ "Signature *" — the near-universal required-field marker. The value then
stayed empty and the exported PDF (and signed-reply attachment) came out blank
for that field, with no error.
"""
import pytest

from src.pdf_form_doc import render_form_as_markdown, parse_markdown_to_values


def test_asterisk_label_value_survives_export_roundtrip():
    fields = [
        {"name": "email", "label": "Email Address *", "type": "text",
         "value": "me@x.com", "page": 1},
        {"name": "state", "label": "State *", "type": "choice",
         "options": ["CA", "NY"], "value": "NY", "page": 1},
        {"name": "sign", "label": "Signature *", "type": "signature",
         "value": "signature:s1", "page": 1},
    ]
    md = render_form_as_markdown(fields, "u", "F")
    vals = parse_markdown_to_values(md)
    assert vals["email"] == "me@x.com"
    assert vals["state"] == "NY"
    assert vals["sign"] == "signature:s1"


def test_plain_labels_and_colon_values_unaffected():
    fields = [
        {"name": "name", "label": "Full Name", "type": "text",
         "value": "Alice", "page": 1},
        {"name": "time", "label": "Start Time", "type": "text",
         "value": "9:00 sharp", "page": 1},
    ]
    md = render_form_as_markdown(fields, "u", "F")
    vals = parse_markdown_to_values(md)
    assert vals["name"] == "Alice"
    assert vals["time"] == "9:00 sharp"


@pytest.mark.parametrize("name", ["Prénom", "Año", "İsim", "氏名", "اسم", "n²", "Cafe\u0301"])
@pytest.mark.parametrize("field_type, value", [
    ("text", "Alice"), ("checkbox", True), ("choice", "Yes"),
])
def test_unicode_field_names_survive_export_roundtrip(name, field_type, value):
    fields = [{"name": name, "label": "Field *", "type": field_type,
               "value": value, "options": ["Yes", "No"], "page": 1}]
    markdown = render_form_as_markdown(fields, "upload", "Form")
    marker_name = markdown.split("<!-- field=", 1)[1].split(" type=", 1)[0]
    assert marker_name.isascii()
    assert parse_markdown_to_values(markdown) == {name: value}


def test_previously_saved_unicode_markers_remain_exportable():
    # Older imports emitted Unicode letters literally. Their persisted
    # markdown must also work after upgrading, without re-importing the PDF.
    markdown = (
        "- **First name:** Alice <!-- field=Prénom type=text -->\n"
        "- [x] **Consent** <!-- field=同意 type=checkbox -->\n"
        "- **Year** [2025 / 2026]: 2026 <!-- field=Año type=choice -->\n"
    )
    assert parse_markdown_to_values(markdown) == {
        "Prénom": "Alice", "同意": True, "Año": "2026",
    }


def test_unicode_names_do_not_collide_with_literal_percent_or_ascii_names():
    names = ["Prénom", "Pr%C3%A9nom", "Prenom", "Section.1_name-2"]
    fields = [{"name": name, "type": "text", "value": str(i), "page": 1}
              for i, name in enumerate(names)]
    markdown = render_form_as_markdown(fields, "upload", "Form")
    assert "field=Section.1_name-2 " in markdown
    assert parse_markdown_to_values(markdown) == {name: str(i) for i, name in enumerate(names)}
