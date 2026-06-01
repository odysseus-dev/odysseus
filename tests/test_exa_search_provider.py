import httpx

from services.search import core as services_search_core
from services.search import providers as services_search_providers
from src.search import core as src_search_core
from src.search import providers as src_search_providers


class _DummyResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://api.exa.ai/search")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("bad status", request=request, response=response)

    def json(self):
        return self._payload


def test_src_exa_search_uses_api_key_and_parses_results(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["json"] = json
        seen["headers"] = headers
        seen["timeout"] = timeout
        return _DummyResponse({
            "results": [
                {
                    "title": "Exa result",
                    "url": "https://example.com/post",
                    "text": "Useful body text for the snippet.",
                    "publishedDate": "2026-06-01T00:00:00.000Z",
                }
            ]
        })

    monkeypatch.setattr(src_search_providers, "_get_provider_key", lambda provider: "exa-test-key")
    monkeypatch.setattr(src_search_providers.httpx, "post", fake_post)

    results = src_search_providers.exa_search("odysseus", count=3, time_filter="week")

    assert len(results) == 1
    assert results[0]["title"] == "Exa result"
    assert results[0]["url"] == "https://example.com/post"
    assert results[0]["snippet"] == "Useful body text for the snippet."
    assert results[0]["age"] == "2026-06-01T00:00:00.000Z"
    assert seen["url"] == "https://api.exa.ai/search"
    assert seen["headers"]["x-api-key"] == "exa-test-key"
    assert seen["json"]["query"] == "odysseus"
    assert seen["json"]["type"] == "auto"
    assert seen["json"]["numResults"] == 3
    assert seen["json"]["text"] is True
    assert seen["json"]["category"] == "news"
    assert "startPublishedDate" in seen["json"]


def test_services_exa_search_returns_empty_without_key(monkeypatch):
    monkeypatch.setattr(services_search_providers, "_get_provider_key", lambda provider: "")

    assert services_search_providers.exa_search("odysseus") == []


def test_src_core_dispatches_to_exa(monkeypatch):
    monkeypatch.setattr(src_search_core, "exa_search", lambda query, count, time_filter=None: [{"url": "https://exa.test"}])

    results = src_search_core._call_provider("exa", "odysseus", 5, "month")

    assert results == [{"url": "https://exa.test"}]


def test_services_core_dispatches_to_exa(monkeypatch):
    monkeypatch.setattr(services_search_core, "exa_search", lambda query, count, time_filter=None: [{"url": "https://exa.test"}])

    results = services_search_core._call_provider("exa", "odysseus", 5, "month")

    assert results == [{"url": "https://exa.test"}]
