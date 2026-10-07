"""Blocked search responses retain diagnostics without changing empty results."""

import sys
from types import SimpleNamespace

import httpx
import pytest

from services.search import providers
from services.search.analytics import ProviderUnavailableError


@pytest.fixture
def upstream(monkeypatch):
    responses = []
    requests = []

    def handle(request):
        requests.append(request)
        assert responses, "Unexpected search request"
        status, payload = responses.pop(0)
        if isinstance(payload, dict):
            return httpx.Response(status, json=payload)
        return httpx.Response(status, text=payload)

    monkeypatch.setattr(providers, "_get_search_instance", lambda: "https://search.test")
    monkeypatch.setattr(providers, "_safesearch_for", lambda _: "2")
    monkeypatch.setattr(providers, "_GENERAL_ENGINES", "fixture,other")
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(providers.httpx, "get", client.get)
        yield responses, requests


def test_searxng_explicit_engines_exclude_default_categories(upstream):
    responses, requests = upstream
    responses.append((200, {"results": [{"url": "https://source.test", "title": "Source"}]}))

    assert providers.searxng_search_api("knowledge distillation")[0]["url"] == "https://source.test"
    assert requests[0].url.params["engines"] == "fixture,other"
    assert "categories" not in requests[0].url.params


def test_requested_engines_preserve_publication_metadata_and_date_filter(upstream):
    responses, requests = upstream
    responses.append((200, {"results": [{
        "url": "https://source.test/paper", "title": "Paper",
        "engines": ["semantic scholar"], "publishedDate": "2026-10-01",
    }]}))

    rows = providers.searxng_search_api(
        "knowledge distillation", engines="semantic scholar", time_filter="month",
    )

    assert requests[0].url.params["engines"] == "semantic scholar"
    assert requests[0].url.params["time_range"] == "month"
    assert "categories" not in requests[0].url.params
    assert rows[0]["published_date"] == "2026-10-01"
    assert rows[0]["engines"] == ["semantic scholar"]
    assert rows[0]["provider"] == "searxng"
    assert rows[0]["query"] == "knowledge distillation"


def test_searxng_preserves_engine_failures_across_empty_retries(upstream):
    responses, requests = upstream
    blocked = {"results": [], "unresponsive_engines": [["brave", "HTTP 429"], ["google", "CAPTCHA"]]}
    responses.extend([(200, blocked), (200, blocked), (200, {"results": []})])

    with pytest.raises(ProviderUnavailableError) as error:
        providers.searxng_search_api("knowledge distillation")

    assert "brave: HTTP 429" in str(error.value)
    assert "google: CAPTCHA" in str(error.value)
    assert str(error.value).count("brave:") == 1
    assert "engines" not in requests[-1].url.params
    assert requests[-1].url.params["categories"] == "general"


def test_searxng_successful_fallback_ignores_prior_engine_errors(upstream):
    responses, requests = upstream
    blocked = {"results": [], "unresponsive_engines": [["fixture", "CAPTCHA"]]}
    responses.extend([
        (200, blocked), (200, blocked),
        (200, {"results": [{"url": "https://source.test", "content": "Useful evidence"}]}),
    ])

    assert providers.searxng_search_api("knowledge distillation") == [
        {"url": "https://source.test", "title": "", "snippet": "Useful evidence",
         "provider": "searxng", "engines": [], "published_date": None,
         "query": "knowledge distillation"},
    ]
    assert len(requests) == 3


def test_searxng_genuine_empty_search_remains_empty(upstream):
    responses, _ = upstream
    responses.extend([(200, {"results": []})] * 3)

    assert providers.searxng_search_api("rare nonexistent phrase") == []


def test_news_fallback_also_selects_only_explicit_engines(upstream):
    responses, requests = upstream
    responses.extend([
        (200, {"results": []}),
        (200, {"results": [{"url": "https://source.test"}]}),
    ])

    assert providers.searxng_search_api("latest distillation news")
    assert requests[0].url.params["categories"] == "news"
    assert requests[1].url.params["engines"] == "fixture,other"
    assert "categories" not in requests[1].url.params


def _empty_ddgs(monkeypatch):
    monkeypatch.setitem(sys.modules, "ddgs", SimpleNamespace(
        DDGS=lambda: SimpleNamespace(text=lambda *args, **kwargs: []),
    ))


@pytest.mark.parametrize("status,html,detail", [
    (202, "<html>No results</html>", "HTTP 202"),
    (200, '<form id="challenge-form">Verify</form>', "CAPTCHA"),
    (202, '<form id="anomaly-form">Verify</form>', "CAPTCHA"),
])
def test_duckduckgo_blocked_empty_response_is_reported(upstream, monkeypatch, status, html, detail):
    responses, requests = upstream
    _empty_ddgs(monkeypatch)
    responses.append((status, html))

    with pytest.raises(ProviderUnavailableError, match=detail):
        providers.duckduckgo_search("knowledge distillation")

    assert len(requests) == 1


def test_duckduckgo_genuine_empty_page_remains_empty(upstream, monkeypatch):
    responses, _ = upstream
    _empty_ddgs(monkeypatch)
    responses.append((200, "<html>No matching pages</html>"))

    assert providers.duckduckgo_search("rare nonexistent phrase") == []


def test_duckduckgo_results_are_not_rejected_for_status_202(upstream, monkeypatch):
    responses, _ = upstream
    _empty_ddgs(monkeypatch)
    responses.append((202, '<div class="result"><a class="result__a" href="https://source.test">Source</a></div>'))

    assert providers.duckduckgo_search("knowledge distillation")[0]["url"] == "https://source.test"
