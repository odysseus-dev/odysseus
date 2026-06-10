from src.pdf_form_doc import _format_field_bullet


def _field(options):
    return {"name": "fld", "type": "choice", "label": "Pick", "value": "", "options": options}


def test_format_field_bullet_handles_non_list_options():
    # options is loaded from a .fields.json sidecar on disk; a corrupt/hand-edited
    # value can be a non-iterable (int) and the old " / ".join(opts) then crashed.
    out = _format_field_bullet(_field(123))
    assert "[]" in out


def test_format_field_bullet_stringifies_list_options():
    # a list with a non-string item also crashed str.join on the old code.
    out = _format_field_bullet(_field(["A", 2, "C"]))
    assert "[A / 2 / C]" in out
