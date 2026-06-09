"""Regression: visual_report markdown helpers must tolerate a non-string.

_autolink_urls did `re.sub(..., md_text)` and _extract_headings did
`re.finditer(..., md_text)`; a None/non-string raised TypeError. They now
return the input / [] respectively.
"""
from src.visual_report import _autolink_urls, _extract_headings


def test_non_string_does_not_crash():
    assert _autolink_urls(None) is None
    assert _extract_headings(None) == []
    assert _extract_headings(123) == []


def test_valid_markdown_unchanged():
    assert "](https://x.com)" in _autolink_urls("see https://x.com")
    assert _extract_headings("## Title")[0]["text"] == "Title"
