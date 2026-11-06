"""You.com search provider: keyless / keyed dispatch, parsing, and error paths.

No network. Mocks httpx so CI stays offline.
"""

import json

import httpx
import pytest

from services.search import core, providers


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://ydc-index.io/v1/search")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"{self.status_code}", request=request, response=response
            )

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("Expecting value", self.text or "", 0)
        return self._payload


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    monkeypatch.delenv("YDC_API_KEY", raising=False)
    for name in ("DATA_BRAVE_API_KEY", "BRAVE_API_KEY", "TAVILY_API_KEY",
                 "SERPER_API_KEY", "GOOGLE_API_KEY", "GOOGLE_PSE_CX"):
        monkeypatch.delenv(name, raising=False)


def _sample_payload(n=2):
    return {
        "results": {
            "web": [
                {
                    "url": f"https://example.com/{i}",
                    "title": f"Title {i}",
                    "description": f"Desc {i}",
                    "snippets": [f"Snippet A{i}", f"Snippet B{i}"],
                    "page_age": "2026-10-01T00:00:00",
                }
                for i in range(n)
            ]
        },
        "metadata": {"query": "q"},
    }


def test_provider_info_marks_you_com_keyless():
    label, needs_key, needs_url = providers.PROVIDER_INFO["you_com"]
    assert label == "You.com"
    assert needs_key is False
    assert needs_url is False


def test_provider_configured_without_key():
    assert providers.provider_configured("you_com") is True


def test_keyless_posts_to_agents_search(monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        seen["json"] = kwargs.get("json") or {}
        return _Resp(payload=_sample_payload())

    monkeypatch.setattr(providers.httpx, "post", post)
    results = providers.you_com_search("latest rust release", count=2)

    assert seen["url"] == providers._YOU_COM_FREE_URL
    assert "X-API-Key" not in seen["headers"]
    assert seen["headers"]["User-Agent"] == providers._YOU_COM_UA
    assert seen["json"]["query"] == "latest rust release"
    assert seen["json"]["count"] == 2
    assert seen["json"]["safesearch"] == "strict"
    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/0"
    assert results[0]["title"] == "Title 0"
    assert "Snippet A0" in results[0]["snippet"]
    assert results[0]["age"] == "2026-10-01T00:00:00"


def test_keyed_posts_to_ydc_index_with_api_key(monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "secret-key")
    seen = {}

    def post(url, **kwargs):
        seen["url"] = url
        seen["headers"] = kwargs.get("headers") or {}
        seen["json"] = kwargs.get("json") or {}
        return _Resp(payload=_sample_payload(1))

    monkeypatch.setattr(providers.httpx, "post", post)
    results = providers.you_com_search("canada news", count=5, time_filter="week")

    assert seen["url"] == providers._YOU_COM_KEYED_URL
    assert seen["headers"]["X-API-Key"] == "secret-key"
    assert seen["headers"]["User-Agent"] == providers._YOU_COM_UA
    assert seen["json"]["freshness"] == "week"
    assert len(results) == 1


def test_settings_key_preferred_over_env(monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "env-key")
    monkeypatch.setattr(
        providers, "_get_search_settings",
        lambda: {"search_safesearch": "moderate", "ydc_api_key": "settings-key"},
    )
    seen = {}

    def post(url, **kwargs):
        seen["headers"] = kwargs.get("headers") or {}
        seen["json"] = kwargs.get("json") or {}
        return _Resp(payload=_sample_payload(1))

    monkeypatch.setattr(providers.httpx, "post", post)
    providers.you_com_search("q", count=1)
    assert seen["headers"]["X-API-Key"] == "settings-key"
    assert seen["json"]["safesearch"] == "moderate"


def test_legacy_shared_key_ignored_for_you_com(monkeypatch):
    monkeypatch.setattr(
        providers, "_get_search_settings",
        lambda: {"search_api_key": "stale-other-provider-key"},
    )
    assert providers._get_provider_key("you_com") == ""


def test_malformed_json_returns_empty(monkeypatch):
    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: _Resp(payload=None, text="<html>"))
    assert providers.you_com_search("hello") == []


def test_rate_limit_returns_empty(monkeypatch):
    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: _Resp(status_code=429))
    assert providers.you_com_search("hello") == []


def test_http_error_returns_empty(monkeypatch):
    monkeypatch.setenv("YDC_API_KEY", "bad")
    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: _Resp(status_code=401))
    assert providers.you_com_search("hello") == []


def test_network_error_returns_empty(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(providers.httpx, "post", boom)
    assert providers.you_com_search("hello") == []


def test_description_fallback_when_no_snippets(monkeypatch):
    payload = {
        "results": {
            "web": [{"url": "https://x.test", "title": "T", "description": "Only desc", "snippets": []}]
        }
    }
    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: _Resp(payload=payload))
    results = providers.you_com_search("q", count=1)
    assert results[0]["snippet"] == "Only desc"


def test_news_fallback_when_web_empty(monkeypatch):
    payload = {
        "results": {
            "web": [],
            "news": [{"url": "https://news.test", "title": "N", "description": "D"}],
        }
    }
    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: _Resp(payload=payload))
    results = providers.you_com_search("q", count=3)
    assert results == [{"title": "N", "url": "https://news.test", "snippet": "D", "age": ""}]


def test_count_clamped_to_api_max(monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen["json"] = kwargs.get("json") or {}
        return _Resp(payload={"results": {"web": []}})

    monkeypatch.setattr(providers.httpx, "post", post)
    providers.you_com_search("q", count=500)
    assert seen["json"]["count"] == 100


def test_call_provider_dispatches_you_com(monkeypatch):
    called = {}

    def fake(query, count=None, time_filter=None):
        called["args"] = (query, count, time_filter)
        return [{"title": "t", "url": "https://x", "snippet": "s"}]

    monkeypatch.setattr(core, "you_com_search", fake)
    out = core._call_provider("you_com", "hello", 4, "day")
    assert called["args"] == ("hello", 4, "day")
    assert out[0]["url"] == "https://x"


def test_you_com_stays_in_fallback_chain_without_key(monkeypatch):
    monkeypatch.setattr(core, "_get_search_settings", lambda: {"search_fallback_chain": ["you_com"]})
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})
    monkeypatch.setattr(providers, "_get_provider_key", lambda name: "")
    assert core._build_provider_chain("searxng") == ["searxng", "searxng_yep", "you_com"]


def test_safesearch_mapping(monkeypatch):
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "off"})
    assert providers._safesearch_for("you_com") == "off"
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    assert providers._safesearch_for("you_com") == "strict"


# ── Settings save/load ──

def test_default_settings_keep_searxng_and_add_optional_key():
    from src.settings import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["search_provider"] == "searxng"
    assert DEFAULT_SETTINGS["search_fallback_chain"] == ["duckduckgo"]
    assert DEFAULT_SETTINGS["ydc_api_key"] == ""


def test_settings_round_trip_feeds_provider_key(tmp_path, monkeypatch):
    from src import settings

    settings_file = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(settings_file))
    settings._invalidate_caches()
    try:
        current = settings.load_settings()
        settings.save_settings({**current, "search_provider": "you_com", "ydc_api_key": "saved-key"})
        loaded = settings.load_settings()
        assert loaded["search_provider"] == "you_com"
        assert loaded["ydc_api_key"] == "saved-key"

        # Provider reads the persisted key through the normal settings path.
        monkeypatch.setattr(providers, "_get_search_settings", lambda: dict(settings.load_settings()))
        assert providers._get_provider_key("you_com") == "saved-key"
    finally:
        settings._invalidate_caches()


def test_ydc_api_key_is_scrubbed_and_not_returned_by_search_config(monkeypatch):
    from src.settings_scrub import scrub_settings

    assert scrub_settings({"ydc_api_key": "k"})["ydc_api_key"] == ""

    settings = {"search_provider": "you_com", "ydc_api_key": "k"}
    monkeypatch.setattr(core, "_get_search_settings", lambda: settings)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: settings)
    config = core.get_search_config()
    assert config["active_provider"] == "you_com"
    assert config["has_api_key"] is True
    assert "k" not in config.values()
