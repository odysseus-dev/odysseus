"""Regression: _extract_entities must find non-ASCII capitalized names.

The name extractor used the ASCII-only class [A-Z][a-zA-Z]+, so a query like
"İstanbul weather" or "Zürich hotels" yielded no name entities at all, and
"São Paulo" lost "São" — non-English/accented place and proper names were
silently dropped from query enhancement. Detection is now Unicode-aware;
ASCII behaviour (including camelCase mid-word capitals not counting as names)
is preserved.
"""
from services.search.query import _extract_entities


def _names(q):
    return _extract_entities(q)["names"]


def test_non_ascii_names_are_extracted():
    assert "İstanbul" in _names("İstanbul weather")
    assert "Zürich" in _names("Zürich hotels")
    assert set(_names("trip to São Paulo")) >= {"São", "Paulo"}


def test_ascii_names_unchanged():
    assert _names("What did Alice do in 2024") == ["Alice"]
    assert _names("news about OpenAI and Google") == ["OpenAI", "Google"]


def test_lowercase_camelcase_and_numbers_are_not_names():
    assert _names("the iphone price") == []
    assert _names("iPhone price") == []       # mid-word capital is not a name
    assert _names("top 50 albums") == []
