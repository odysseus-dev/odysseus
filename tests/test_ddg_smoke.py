"""Smoke test: duckduckgo_search() returns results via the real ddgs package."""
import pytest

ddgs_lib = pytest.importorskip("ddgs", reason="ddgs not installed")

from services.search.providers import duckduckgo_search


@pytest.mark.slow
def test_ddg_search_returns_results_with_real_library():
    """Integration: real DDGS().text() round-trip — needs network + ddgs package."""
    results = duckduckgo_search("python programming language", count=3)
    assert isinstance(results, list)
    assert len(results) >= 1
    first = results[0]
    assert "title" in first and first["title"]
    assert "url" in first and first["url"].startswith("http")
    assert "snippet" in first
