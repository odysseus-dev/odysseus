"""Regression: search query helpers must tolerate non-string input.

Each helper called a string method (`.strip()`, `.lower()`) or `re.*` directly
on the query, so a non-string (None / number) raised AttributeError/TypeError.
They now coerce/short-circuit on non-strings.
"""
from services.search.query import (
    _detect_question_type,
    _split_multi_part,
    _extract_site_filter,
    enhance_query,
    _is_news_query,
)


def test_detect_question_type_non_string():
    assert _detect_question_type(None) is None
    assert _detect_question_type(123) is None


def test_split_multi_part_non_string():
    assert _split_multi_part(None) == []


def test_extract_site_filter_non_string():
    assert _extract_site_filter(None) == ("", None)


def test_is_news_query_non_string():
    assert _is_news_query(None) is False


def test_enhance_query_non_string_does_not_crash():
    out = enhance_query(None)
    assert isinstance(out, tuple) and len(out) == 2


def test_valid_query_still_works():
    assert _is_news_query("latest news on rates") is True
    assert _detect_question_type("what is rust") == "what"
