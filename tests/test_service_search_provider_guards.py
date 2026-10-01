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


# ── SearXNG language pin (#6392) ──

def _searxng_result(url, title="Title", snippet="Snippet"):
    return {"title": title, "url": url, "content": snippet}


def _fake_searxng_get(responses):
    """Script searxng_search_api's HTTP calls; entries are result lists or exceptions.

    Returns (calls, fake_get); `calls` collects the params of every request made,
    so a test can assert both the fallback chain and its absence.
    """
    calls = []

    def fake_get(url, **kwargs):
        calls.append(dict(kwargs["params"]))
        response_body = responses[len(calls) - 1]
        if isinstance(response_body, Exception):
            raise response_body

        class _Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": response_body}

        return _Response()

    return calls, fake_get


def _patch_searxng(monkeypatch, settings, fake_get):
    monkeypatch.setattr(providers, "_get_search_instance", lambda: "http://searx.test")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: settings)
    monkeypatch.delenv("SEARXNG_LANGUAGE", raising=False)
    monkeypatch.setattr(providers.httpx, "get", fake_get)


def test_service_searxng_language_resolution_precedence(monkeypatch):
    monkeypatch.delenv("SEARXNG_LANGUAGE", raising=False)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})
    assert providers._get_search_language() == "en"

    monkeypatch.setenv("SEARXNG_LANGUAGE", "all")
    assert providers._get_search_language() is None

    monkeypatch.setenv("SEARXNG_LANGUAGE", "DE")
    assert providers._get_search_language() == "de"

    # An explicit admin setting wins over the env var.
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_language": "fr"})
    assert providers._get_search_language() == "fr"


def test_service_searxng_sends_no_language_when_unpinned(monkeypatch):
    calls, fake_get = _fake_searxng_get([[_searxng_result("https://example.com/roomba")]])
    _patch_searxng(monkeypatch, {"search_language": "all"}, fake_get)

    assert providers.searxng_search_api("roomba saugroboter", count=1)

    assert len(calls) == 1
    assert "language" not in calls[0]


def test_service_searxng_irrelevant_pinned_results_retry_without_language(monkeypatch):
    # The #6392 reproduction: Bing answers a German query sent with language=en
    # with non-empty filler, so every `not parsed` fallback stays silent.
    filler = _searxng_result(
        "https://www.facebook.com/Budissa.Bautzen/",
        "Budissa Bautzen",
        "Wir sind ein Backwarenfachgeschäft",
    )
    relevant = _searxng_result(
        "https://www.stiftung-warentest.de/saugroboter/",
        "Saugroboter im Test",
        "Stromverbrauch und Wasserwechsel",
    )
    calls, fake_get = _fake_searxng_get([[filler], [relevant]])
    _patch_searxng(monkeypatch, {"search_safesearch": "strict"}, fake_get)

    results = providers.searxng_search_api(
        "Saugroboter mit Wasserwechselstation Preis Deutschland", count=1
    )

    assert len(calls) == 2
    assert calls[0]["language"] == "en"
    assert "language" not in calls[1]
    assert results[0]["url"] == relevant["url"]


def test_service_searxng_relevant_pinned_results_make_no_second_request(monkeypatch):
    calls, fake_get = _fake_searxng_get(
        [[_searxng_result("https://example.com/odysseus", "Odysseus", "hero of the Odyssey")]]
    )
    _patch_searxng(monkeypatch, {"search_safesearch": "strict"}, fake_get)

    assert providers.searxng_search_api("odysseus", count=1)

    assert len(calls) == 1
    assert calls[0]["language"] == "en"


def test_service_searxng_keeps_pinned_results_when_retry_is_also_irrelevant(monkeypatch):
    first = _searxng_result("https://example.com/a", "Unrelated one", "filler")
    second = _searxng_result("https://example.com/b", "Unrelated two", "more filler")
    calls, fake_get = _fake_searxng_get([[first], [second]])
    _patch_searxng(monkeypatch, {"search_safesearch": "strict"}, fake_get)

    results = providers.searxng_search_api("wasserwechselstation", count=1)

    assert len(calls) == 2
    assert results[0]["url"] == first["url"]


def test_service_searxng_failed_unpinned_retry_keeps_pinned_results(monkeypatch):
    # The outer handler would replace already-received results with an HTML
    # scrape, so a retry that errors must not propagate.
    first = _searxng_result("https://example.com/a", "Unrelated one", "filler")
    calls, fake_get = _fake_searxng_get([[first], RuntimeError("searxng down")])
    _patch_searxng(monkeypatch, {"search_safesearch": "strict"}, fake_get)

    results = providers.searxng_search_api("wasserwechselstation", count=1)

    assert len(calls) == 2
    assert results[0]["url"] == first["url"]
