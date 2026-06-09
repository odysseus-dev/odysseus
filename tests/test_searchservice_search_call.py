"""Regression: SearchService.search() must call the (synchronous)
comprehensive_web_search correctly and return structured results.

The wrapper previously did:

    raw_results = await comprehensive_web_search(
        query, max_results=10 * depth, fetch_content=fetch_content)

which is broken three ways:
  * comprehensive_web_search is a plain `def` (sync), so `await` on its return
    raised TypeError;
  * it accepts neither `max_results` nor `fetch_content` (the real knob is
    `max_pages`), so the call raised TypeError on binding before running;
  * it returns a context string (or a (context, sources) tuple), not the list
    of dicts the wrapper then iterates.

SearchService.search is exported via services/search/__init__.py and
services/__init__.py (with a usage example in its own docstring), so this is a
broken public API method. This test drives it with a stubbed search backend.
"""
import asyncio

from services.search import service as search_service
from services.search.service import SearchService, SearchResponse


def test_search_returns_structured_results(monkeypatch):
    calls = {}

    def fake_search(query, max_pages=3, return_sources=False, **kwargs):
        calls["query"] = query
        calls["max_pages"] = max_pages
        calls["return_sources"] = return_sources
        calls["kwargs"] = kwargs
        sources = [{"url": "https://example.com", "title": "Example"}]
        return ("context text", sources) if return_sources else "context text"

    monkeypatch.setattr(search_service, "comprehensive_web_search", fake_search)

    svc = SearchService(default_depth=2)
    resp = asyncio.run(svc.search("python async patterns"))

    assert isinstance(resp, SearchResponse)
    assert resp.total == 1
    assert resp.results[0].url == "https://example.com"
    assert resp.results[0].title == "Example"

    # Called with the real param (max_pages, not max_results) and asked for the
    # structured source list rather than the context string.
    assert calls["return_sources"] is True
    assert calls["max_pages"] == 20  # 10 * depth(2)
    assert "max_results" not in calls["kwargs"]
    assert "fetch_content" not in calls["kwargs"]
