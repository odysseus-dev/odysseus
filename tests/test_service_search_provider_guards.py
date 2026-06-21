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

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "off"})
    monkeypatch.setitem(sys.modules, "ddgs", None)
    monkeypatch.setattr(providers.httpx, "get", fake_get)

    results = providers.duckduckgo_search("odysseus", count=1)

    assert seen["params"]["kp"] == "-2"
    assert results[0]["url"].startswith("https://notduckduckgo.com/")


def _capture_searxng_params(monkeypatch, query, **kw):
    seen = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": "R", "url": "https://example.com", "content": "S"}]}

    def fake_get(url, **kwargs):
        seen["params"] = kwargs["params"]
        return _Response()

    monkeypatch.setattr(providers, "_get_search_instance", lambda: "http://searx.test")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    monkeypatch.setattr(providers.httpx, "get", fake_get)
    providers.searxng_search_api(query, **kw)
    return seen["params"]


def test_service_searxng_general_query_uses_auto_language_no_engine_pin(monkeypatch):
    """A non-English query must not be forced to English, and a general query must
    not pin engines — the regression that buried 'resultado MotoGP hoje' under
    SpanishDict / lottery / Lusa pages instead of the actual race results."""
    monkeypatch.setattr(providers, "_SEARCH_LANGUAGE", "auto")
    monkeypatch.setattr(providers, "_GENERAL_ENGINES", "")
    params = _capture_searxng_params(monkeypatch, "resultado MotoGP hoje", count=3)
    assert params.get("language") == "auto"
    assert "engines" not in params
    assert params["q"] == "resultado MotoGP hoje"


def test_service_searxng_empty_language_omits_param(monkeypatch):
    monkeypatch.setattr(providers, "_SEARCH_LANGUAGE", "")
    monkeypatch.setattr(providers, "_GENERAL_ENGINES", "")
    params = _capture_searxng_params(monkeypatch, "odysseus", count=1)
    assert "language" not in params


def test_service_search_defaults_are_not_english_or_bing_pinned():
    """Defaults must not regress to the hard 'en' pin or the bad engine pin."""
    assert providers._SEARCH_LANGUAGE != "en"
    assert "bing" not in providers._GENERAL_ENGINES
