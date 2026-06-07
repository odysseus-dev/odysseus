"""Regression tests for the canonical services.search provider implementation.

The old src.search provider path aliases this module; these tests pin the
behavior at the single implementation point.
"""

import sys

from services.search import providers


def test_service_safesearch_values_match_provider_contract(monkeypatch):
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    assert providers._safesearch_for("searxng") == "2"
    assert providers._safesearch_for("brave") == "strict"
    assert providers._safesearch_for("duckduckgo_lib") == "on"
    assert providers._safesearch_for("duckduckgo_html") == "1"
    assert providers._safesearch_for("google_pse") == "active"
    assert providers._safesearch_for("serper") == "active"

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "off"})
    assert providers._safesearch_for("searxng") == "0"
    assert providers._safesearch_for("brave") == "off"
    assert providers._safesearch_for("duckduckgo_lib") == "off"
    assert providers._safesearch_for("duckduckgo_html") == "-2"
    assert providers._safesearch_for("google_pse") is None
    assert providers._safesearch_for("serper") is None


def test_service_searxng_json_sends_safesearch(monkeypatch):
    seen = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"title": "Result", "url": "https://example.com", "content": "Snippet"}
                ]
            }

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs["params"]
        return _Response()

    monkeypatch.setattr(providers, "_get_search_instance", lambda: "http://searx.test")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "moderate"})
    monkeypatch.setattr(providers.httpx, "get", fake_get)

    results = providers.searxng_search_api("odysseus", count=1)

    assert results
    assert seen["url"] == "http://searx.test/search"
    assert seen["params"]["safesearch"] == "1"


def test_service_ddg_redirect_ignores_lookalike_hosts():
    for host in ("duckduckgo.com.evil.com", "notduckduckgo.com"):
        url = f"https://{host}/l/?uddg=https%3A%2F%2Fexample.com"
        assert providers._resolve_ddg_redirect(url) == url

    assert providers._resolve_ddg_redirect(
        "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com"
    ) == "https://example.com"


def test_service_ddg_html_fallback_sends_safesearch(monkeypatch):
    seen = {}
    html = """
    <html><body>
      <div class="result">
        <a class="result__a" href="https://notduckduckgo.com/l/?uddg=https%3A%2F%2Fevil.example">
          Lookalike
        </a>
        <a class="result__snippet">Snippet</a>
      </div>
    </body></html>
    """

    class _Response:
        text = html

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        seen["params"] = kwargs["params"]
        return _Response()

    monkeypatch.setitem(sys.modules, "duckduckgo_search", None)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "off"})
    monkeypatch.setattr(providers.httpx, "get", fake_get)

    results = providers.duckduckgo_search("odysseus", count=1)

    assert seen["params"]["kp"] == "-2"
    assert results[0]["url"].startswith("https://notduckduckgo.com/")


def test_service_perplexity_search_payload_and_parsing(monkeypatch):
    seen = {}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"title": "T1", "url": "https://example.com/a", "snippet": "S1", "date": "2026-01-02"},
                    {"title": "T2", "url": "https://example.com/b", "snippet": "S2", "last_updated": "2026-01-03"},
                    {"title": "no url", "url": "", "snippet": "dropped"},
                ]
            }

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["json"] = kwargs["json"]
        seen["headers"] = kwargs["headers"]
        return _Response()

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"perplexity_api_key": "pplx-test"})
    monkeypatch.setattr(providers.httpx, "post", fake_post)

    results = providers.perplexity_search("odysseus", count=50, time_filter="week")

    # endpoint + bearer auth
    assert seen["url"] == "https://api.perplexity.ai/search"
    assert seen["headers"]["Authorization"] == "Bearer pplx-test"
    # payload: max_results capped at 20, time_filter -> search_recency_filter
    assert seen["json"]["query"] == "odysseus"
    assert seen["json"]["max_results"] == 20
    assert seen["json"]["search_recency_filter"] == "week"
    # parsing: url-less result dropped; date/last_updated -> age
    assert [r["url"] for r in results] == ["https://example.com/a", "https://example.com/b"]
    assert results[0]["age"] == "2026-01-02"
    assert results[1]["age"] == "2026-01-03"
