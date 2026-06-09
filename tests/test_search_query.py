"""Tests for research query entity extraction (src/search/query.py)."""

from src.search.query import _extract_entities


def test_extracts_full_four_digit_year():
    # Regression: the year pattern used a capturing group `(19|20)`, so
    # re.findall returned just the century ("20") instead of the full year.
    entities = _extract_entities("What happened to OpenAI in 2024")
    assert "2024" in entities["dates"]
    assert "20" not in entities["dates"]


def test_extracts_multiple_years():
    entities = _extract_entities("Compare revenue in 1999 and 2008")
    assert entities["dates"] == ["1999", "2008"]


def test_no_false_year_from_other_numbers():
    entities = _extract_entities("Top 50 albums of all time")
    assert entities["dates"] == []
