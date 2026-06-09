"""Tests for extract_quotes (src/search/content.py)."""
import pytest

pytest.importorskip("bs4")  # content.py imports BeautifulSoup at module load

from src.search.content import extract_quotes


def test_matched_double_quotes():
    assert extract_quotes('She said "this is a proper long quote" today') == [
        "this is a proper long quote"
    ]


def test_matched_single_quotes():
    assert extract_quotes("He wrote 'another sufficiently long quote' here") == [
        "another sufficiently long quote"
    ]


def test_mismatched_quotes_are_not_extracted():
    # Regression: `"text'` (open double, close single) used to be accepted
    # because the closing quote wasn't required to match the opening one.
    assert extract_quotes("""apostrophe d'accord then a "dangling long opener""") == []


def test_short_quotes_ignored():
    assert extract_quotes('say "too short" please') == []
