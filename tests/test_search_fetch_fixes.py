"""#344 follow-ups for deep research search/fetch.

- The duckduckgo-search *library* path returns /l/?uddg= redirect wrappers
  too, not just the HTML fallback, so resolve them there as well. (The
  resolver itself is covered by test_ddg_redirect_resolution.)
- fetch_webpage_content must return an empty result on a 4xx/5xx instead of
  letting httpx.HTTPStatusError escape and crash the fetch.
"""
import sys
import types

import httpx

from services.search import providers
from src.search import content


def test_library_path_resolves_ddg_redirect(monkeypatch):
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Farxiv.org%2Fabs%2F2304.06877&rut=x"

    class _FakeDDGS:
        def text(self, *a, **k):
            return [{"title": "t", "href": wrapped, "body": "b"}]

    fake = types.ModuleType("duckduckgo_search")
    fake.DDGS = _FakeDDGS
    monkeypatch.setitem(sys.modules, "duckduckgo_search", fake)
    monkeypatch.setattr(providers, "_safesearch_for", lambda *a, **k: "moderate")

    results = providers.duckduckgo_search("anything", count=1)
    assert results and results[0]["url"] == "https://arxiv.org/abs/2304.06877"


def test_http_status_error_returns_empty_result(monkeypatch):
    class _Resp:
        status_code = 403
        headers = {}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "403 Forbidden",
                request=httpx.Request("GET", "https://example.test/x"),
                response=self,
            )

    monkeypatch.setattr(content, "_get_public_url", lambda url, *, headers, timeout: _Resp())
    out = content.fetch_webpage_content("https://example.test/x", timeout=1)
    assert out["success"] is False
    assert "403" in out["error"]
