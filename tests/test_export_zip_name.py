"""Document export-zip must append the language extension correctly.

The entry-name logic was `base if "." in base else base + ext`, so any title
containing a dot ("config.local", "report.v2") was assumed to already carry
an extension and the language extension was dropped — a Python doc titled
"config.local" exported as "config.local" instead of "config.local.py".
"""
from routes.document_routes import _export_zip_name


def test_dotted_title_still_gets_language_extension():
    assert _export_zip_name("config.local", ".py", set()) == "config.local.py"
    assert _export_zip_name("report.v2", ".py", set()) == "report.v2.py"


def test_title_already_ending_in_ext_not_doubled():
    assert _export_zip_name("script.py", ".py", set()) == "script.py"
    assert _export_zip_name("notes.txt", ".txt", set()) == "notes.txt"


def test_plain_title_gets_extension():
    assert _export_zip_name("notes", ".txt", set()) == "notes.txt"


def test_dedup_keeps_extension():
    assert _export_zip_name("a", ".py", {"a.py"}) == "a-1.py"
    assert _export_zip_name("config.local", ".py", {"config.local.py"}) == "config.local-1.py"
