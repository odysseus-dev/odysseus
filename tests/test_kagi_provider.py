"""Pin the Kagi provider's v1 API contract (https://kagi.com/api/docs/openapi).

kagi_search must POST to /api/v1/search with Bearer auth and a JSON body
({query, limit, safe_search, filters.after}), and parse web results from the
type-grouped response under data.search.
"""

import pytest

from services.search import providers


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


_KAGI_RESPONSE = {
    "meta": {"id": "req-1", "node": "us-east", "ms": 42},
    "data": {
        "search": [
            {
                "url": "https://example.com/a",
                "title": "Result A",
                "snippet": "First result",
                "time": "2026-01-02",
            },
            {"url": "", "title": "No URL — must be skipped"},
            {"url": "https://example.com/b", "title": "Result B"},
        ],
        "related_search": [{"url": "https://example.com/rel", "title": "ignored"}],
    },
}


@pytest.fixture
def kagi_call(monkeypatch):
    """Capture the httpx.post call kagi_search makes and serve a canned response."""
    calls = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["url"] = url
        calls["json"] = json
        calls["headers"] = headers
        return _FakeResponse(_KAGI_RESPONSE)

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"kagi_api_key": "secret-key"})
    monkeypatch.setattr(providers.httpx, "post", fake_post)
    return calls


def test_kagi_search_request_and_parsing(kagi_call):
    results = providers.kagi_search("odysseus", count=5)

    assert kagi_call["url"] == "https://kagi.com/api/v1/search"
    assert kagi_call["headers"]["Authorization"] == "Bearer secret-key"
    assert kagi_call["json"]["query"] == "odysseus"
    assert kagi_call["json"]["limit"] == 5
    assert kagi_call["json"]["safe_search"] is True  # default level is "strict"
    assert "filters" not in kagi_call["json"]

    assert results == [
        {
            "title": "Result A",
            "url": "https://example.com/a",
            "snippet": "First result",
            "age": "2026-01-02",
        },
        {"title": "Result B", "url": "https://example.com/b", "snippet": "", "age": ""},
    ]


def test_kagi_search_time_filter_maps_to_after_date(kagi_call):
    from datetime import date, timedelta

    providers.kagi_search("odysseus", count=3, time_filter="week")

    after = kagi_call["json"]["filters"]["after"]
    # A week back from today; the one-day window tolerates a midnight crossing
    # between the call and the assertion (ISO dates compare lexicographically).
    lo = (date.today() - timedelta(days=8)).isoformat()
    hi = (date.today() - timedelta(days=7)).isoformat()
    assert lo <= after <= hi


def test_kagi_search_safesearch_off_disables_filter(monkeypatch, kagi_call):
    monkeypatch.setattr(
        providers, "_get_search_settings",
        lambda: {"kagi_api_key": "secret-key", "search_safesearch": "off"},
    )

    providers.kagi_search("odysseus", count=3)

    assert kagi_call["json"]["safe_search"] is False


def test_kagi_search_without_key_returns_empty(monkeypatch):
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})
    monkeypatch.delenv("KAGI_API_KEY", raising=False)
    monkeypatch.setattr(
        providers.httpx, "post",
        lambda *a, **k: pytest.fail("kagi_search must not hit the network without a key"),
    )

    assert providers.kagi_search("odysseus") == []


def test_kagi_search_rate_limit_returns_empty(monkeypatch):
    class _RateLimited(_FakeResponse):
        status_code = 429

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"kagi_api_key": "secret-key"})
    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: _RateLimited({}))

    assert providers.kagi_search("odysseus") == []
