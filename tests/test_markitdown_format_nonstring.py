"""Regression: is_markitdown_format must tolerate a non-string path.

It did `os.path.splitext(path)`, which raises TypeError on None / non-string.
"""
from src.markitdown_runtime import is_markitdown_format


def test_non_string_returns_false():
    assert is_markitdown_format(None) is False
    assert is_markitdown_format(123) is False
    assert is_markitdown_format(["a.docx"]) is False


def test_valid_extension_detected():
    assert is_markitdown_format("report.docx") is True
    assert is_markitdown_format("notes.txt") is False
