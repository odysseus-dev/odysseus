"""Regression: PDF-form markdown export must not drop values whose label
contains an asterisk.

`parse_markdown_to_values` is the read-back path for GET .../export-pdf, the
export preview, and prepare-signed-reply. Its bullet regexes matched the bold
label with `[^*]+`, so they could not match a label like "Email *" / "State *"
/ "Signature *" — the near-universal required-field marker. The value then
stayed empty and the exported PDF (and signed-reply attachment) came out blank
for that field, with no error.
"""
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
