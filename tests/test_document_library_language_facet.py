"""Library language facet must SUM NULL-language and "text" docs.

documents_library built the facet with {lang or "text": cnt ...}, so a
NULL-language row and an explicit "text" row both keyed "text" and one
silently overwrote the other. The language FILTER treats NULL and "text"
as a single bucket ((language == None) | (language == "text")), so the
facet count must add them, otherwise clicking the facet returns more docs
than the count promised.
"""
from routes.document_routes import _aggregate_language_facets


def test_null_and_text_are_summed():
    rows = [(None, 3), ("text", 2), ("python", 5)]
    assert _aggregate_language_facets(rows) == {"text": 5, "python": 5}


def test_only_null():
    assert _aggregate_language_facets([(None, 4)]) == {"text": 4}


def test_distinct_languages_preserved():
    rows = [("python", 2), ("javascript", 7), ("text", 1)]
    assert _aggregate_language_facets(rows) == {"python": 2, "javascript": 7, "text": 1}


def test_empty():
    assert _aggregate_language_facets([]) == {}
