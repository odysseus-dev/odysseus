"""Tests for extract_statistics (src/search/content.py)."""
import pytest

pytest.importorskip("bs4")  # content.py imports BeautifulSoup at module load

from src.search.content import extract_statistics


def test_captures_comma_less_large_number():
    # Regression: `\d{1,3}(?:,\d{3})*` matched only the first 3 digits of a
    # comma-less number, so "50000" was never captured whole.
    assert any(s.startswith("50000") for s in extract_statistics("about 50000 users"))


def test_keeps_percent_sign():
    # Regression: a trailing `\b` after the optional unit dropped the "%".
    assert "12%" in extract_statistics("conversion rose to 12% this quarter")


def test_comma_grouped_number():
    assert any(s.startswith("1,000,000") for s in extract_statistics("revenue of 1,000,000 dollars"))


def test_four_digit_year_captured():
    assert any("2024" in s for s in extract_statistics("released in 2024"))
