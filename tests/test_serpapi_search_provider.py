import httpx
import pytest


def _fake_serpapi_get(captured, *, status_code=200, payload=None):
    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        request = httpx.Request("GET", url, params=params)
        return httpx.Response(
            status_code,
            request=request,
            json=payload if payload is not None else {
                "organic_results": [
                    {
                        "title": "First result",
                        "link": "https://example.com/first",
                        "snippet": "First snippet",
                        "date": "Jun 1, 2026",
                    },
                    {
                        "title": "No URL",
                        "snippet": "Should be skipped",
                    },
                    {
                        "title": "Second result",
                        "link": "https://example.com/second",
                    },
                ]
            },
        )

    return fake_get


@pytest.mark.parametrize("module_name", ["services.search.providers", "src.search.providers"])
def test_serpapi_search_uses_google_light_and_normalizes_results(monkeypatch, module_name):
    providers = pytest.importorskip(module_name)
    captured = {}

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"serpapi_api_key": "serp-key"})
    monkeypatch.setattr(providers.httpx, "get", _fake_serpapi_get(captured))

    results = providers.serpapi_search("coffee", count=3)

    assert captured["url"] == "https://serpapi.com/search.json"
    assert captured["params"] == {
        "engine": "google_light",
        "q": "coffee",
        "api_key": "serp-key",
    }
    assert captured["timeout"] == providers.REQUEST_TIMEOUT
    assert results == [
        {
            "title": "First result",
            "url": "https://example.com/first",
            "snippet": "First snippet",
            "age": "Jun 1, 2026",
        },
        {
            "title": "Second result",
            "url": "https://example.com/second",
            "snippet": "",
            "age": "",
        },
    ]


@pytest.mark.parametrize("module_name", ["services.search.providers", "src.search.providers"])
def test_serpapi_search_returns_empty_without_key(monkeypatch, module_name):
    providers = pytest.importorskip(module_name)

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)

    def fail_get(*args, **kwargs):
        raise AssertionError("SerpApi should not be called without an API key")

    monkeypatch.setattr(providers.httpx, "get", fail_get)

    assert providers.serpapi_search("coffee") == []


@pytest.mark.parametrize("module_name", ["services.search.providers", "src.search.providers"])
def test_serpapi_search_returns_empty_on_rate_limit(monkeypatch, module_name):
    providers = pytest.importorskip(module_name)
    captured = {}

    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"serpapi_api_key": "serp-key"})
    monkeypatch.setattr(providers.httpx, "get", _fake_serpapi_get(captured, status_code=429, payload={}))

    assert providers.serpapi_search("coffee") == []
    assert captured["params"]["engine"] == "google_light"


@pytest.mark.parametrize("module_name", ["services.search.core", "src.search.core"])
def test_call_provider_dispatches_serpapi(monkeypatch, module_name):
    core = pytest.importorskip(module_name)
    seen = {}

    def fake_serpapi_search(query, count, time_filter=None):
        seen["args"] = (query, count, time_filter)
        return [{"title": "ok", "url": "https://example.com", "snippet": ""}]

    monkeypatch.setattr(core, "serpapi_search", fake_serpapi_search)

    assert core._call_provider("serpapi", "query", 7, "week") == [
        {"title": "ok", "url": "https://example.com", "snippet": ""}
    ]
    assert seen["args"] == ("query", 7, "week")


def test_settings_save_load_preserves_serpapi_key(tmp_path, monkeypatch):
    from src import settings as settings_mod

    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", tmp_path / "settings.json")
    settings_mod._invalidate_caches()

    saved = dict(settings_mod.DEFAULT_SETTINGS)
    saved["search_provider"] = "serpapi"
    saved["serpapi_api_key"] = "serp-key"

    settings_mod.save_settings(saved)

    loaded = settings_mod.load_settings()
    assert loaded["search_provider"] == "serpapi"
    assert loaded["serpapi_api_key"] == "serp-key"
