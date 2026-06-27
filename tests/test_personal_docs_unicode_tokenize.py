"""Regression: tokenize() must keep non-ASCII letters as part of words.

tokenize() split on the ASCII-only class [A-Za-z0-9_\\-]+, so any non-ASCII
letter (Turkish ç/ş/ğ/ı/ö/ü, accented Latin é/ï/ü, etc.) acted as a delimiter
and shredded multibyte words into meaningless fragments — "çalışma" became
"al"+"ma", "günü"/"iş" were dropped entirely by the len>1 filter. Since
tokenize() backs both personal-document keyword search and memory retrieval,
this silently broke search for non-English content. It is now Unicode-aware;
ASCII/digit/underscore/hyphen tokenization is byte-for-byte unchanged.
"""
from src.personal_docs import tokenize


def test_turkish_words_are_not_shredded():
    toks = tokenize("Türkçe karakterler çalışma günü")
    assert "türkçe" in toks
    assert "çalışma" in toks
    assert "günü" in toks
    # the old ASCII-only split produced fragments like these:
    assert not ({"rk", "al", "ma", "kar"} & toks)


def test_accented_latin_words_kept_whole():
    toks = tokenize("café résumé naïve")
    assert {"café", "résumé", "naïve"} <= toks


def test_ascii_tokenization_unchanged():
    # hyphen / underscore / digits tokenize exactly as before
    assert tokenize("foo-bar baz_42 qux") == {"foo-bar", "baz_42", "qux"}
