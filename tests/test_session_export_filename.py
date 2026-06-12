"""Regression: _sanitize_export_filename must tolerate a non-string name.

It did `name = name or ""` then `re.sub(..., name)`. A non-string name (e.g. an
int session name) is truthy, so re.sub raised TypeError. Coerce non-strings.
"""
from routes.session_routes import _sanitize_export_filename


def test_non_string_name_does_not_crash():
    assert _sanitize_export_filename(12345) == ""
    assert _sanitize_export_filename(None) == ""


def test_valid_name_sanitized():
    assert _sanitize_export_filename("a/b?c.txt") == "a_b_c.txt"
