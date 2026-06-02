"""Tests for og:image extraction (src/search/content.py)."""
import pytest

pytest.importorskip("bs4")
from bs4 import BeautifulSoup

from src.search.content import _extract_og_image


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_accepts_http_og_image():
    # Regression: only https URLs were returned, so plain-http og:image
    # (still common) yielded no thumbnail despite the docstring promising
    # "http(s)".
    html = '<meta property="og:image" content="http://example.com/cover.jpg">'
    assert _extract_og_image(_soup(html)) == "http://example.com/cover.jpg"


def test_still_accepts_https_og_image():
    html = '<meta property="og:image" content="https://example.com/cover.png">'
    assert _extract_og_image(_soup(html)) == "https://example.com/cover.png"


def test_skips_relative_and_svg():
    html = (
        '<meta property="og:image" content="/relative/logo.png">'
        '<meta name="twitter:image" content="https://example.com/icon.svg">'
    )
    assert _extract_og_image(_soup(html)) == ""
