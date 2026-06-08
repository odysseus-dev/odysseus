"""Regression tests for the canonical services.search provider implementation.

The old src.search provider path aliases this module; these tests pin the
behavior at the single implementation point.
"""

import sys
import types
import warnings

import pytest

from services.search import providers


def test_provider_policy_covers_search_providers():
    for provider_name in providers.PROVIDER_INFO:
        if provider_name == "disabled":
            continue

        policy = providers.get_provider_policy(provider_name)

        assert policy.name == provider_name
        assert policy.label
        assert policy.status_when_empty
        assert policy.query_concurrency in {"parallel", "sequential"}
        assert policy.fallback_concurrency >= 1

    google_policy = providers.get_provider_policy("google_pse")
    assert "google_pse_key" in google_policy.required_settings
    assert "google_pse_cx" in google_policy.required_settings


def test_provider_availability_reports_missing_config_without_secret(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "sk-google-secret")
    monkeypatch.setenv("GOOGLE_PSE_CX", "")
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {
        "google_pse_key": "",
        "google_pse_cx": "",
        "search_api_key": "",
    })

    status = providers.get_provider_availability("google_pse")

    assert status.ok is False
    assert status.reason == "missing_config"
    assert "google_pse_cx" in status.detail
    assert "sk-google-secret" not in status.detail


@pytest.mark.parametrize(
    ("provider_name", "search_fn", "settings", "env_names"),
    [
        ("brave", providers.brave_search, {}, ("DATA_BRAVE_API_KEY",)),
        ("google_pse", providers.google_pse_search, {"google_pse_key": "test-key"}, ("GOOGLE_API_KEY", "GOOGLE_PSE_CX")),
        ("tavily", providers.tavily_search, {}, ("TAVILY_API_KEY",)),
        ("serper", providers.serper_search, {}, ("SERPER_API_KEY",)),
    ],
)
def test_missing_config_does_not_call_provider_network(
    monkeypatch, provider_name, search_fn, settings, env_names
):
    for env_name in env_names:
        monkeypatch.delenv(env_name, raising=False)
    base_settings = {
        "search_api_key": "",
        "brave_api_key": "",
        "google_pse_key": "",
        "google_pse_cx": "",
        "tavily_api_key": "",
        "serper_api_key": "",
    }
    base_settings.update(settings)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: base_settings)

    def fail_network(*args, **kwargs):
        raise AssertionError(f"{provider_name} should not call network with missing config")

    monkeypatch.setattr(providers.httpx, "get", fail_network)
    monkeypatch.setattr(providers.httpx, "post", fail_network)

    assert search_fn("odysseus", count=1) == []


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

    def fake_post(url, **kwargs):
        seen["url"] = url
        seen["data"] = kwargs["data"]
        return _Response()

    monkeypatch.setitem(sys.modules, "duckduckgo_search", None)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "off"})
    monkeypatch.setattr(providers.httpx, "post", fake_post)

    results = providers.duckduckgo_search("odysseus", count=1)

    assert seen["url"] == "https://html.duckduckgo.com/html/"
    assert seen["data"]["kp"] == "-2"
    assert results[0]["url"].startswith("https://notduckduckgo.com/")


def test_duckduckgo_html_fallback_parses_lite_results_when_html_empty(monkeypatch):
    calls = []
    challenge_html = """
    <html><body>
      <p>Unfortunately, bots use DuckDuckGo too.</p>
    </body></html>
    """
    lite_html = """
    <html><body>
      <a href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fresult">
        Lite Result
      </a>
    </body></html>
    """

    class _Response:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        if "html.duckduckgo.com" in url:
            return _Response(challenge_html)
        return _Response(lite_html)

    monkeypatch.setitem(sys.modules, "duckduckgo_search", None)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    monkeypatch.setattr(providers.httpx, "post", fake_post)

    results = providers.duckduckgo_search("long generated query", count=1)

    assert [r["url"] for r in results] == ["https://example.com/result"]
    assert [url for url, _ in calls] == [
        "https://html.duckduckgo.com/html/",
        "https://lite.duckduckgo.com/lite/",
    ]


def _install_fake_ddgs(monkeypatch, text_impl, *, constructor_warning: str = ""):
    fake_mod = types.ModuleType("duckduckgo_search")

    class _FakeDDGS:
        def __init__(self):
            if constructor_warning:
                warnings.warn(constructor_warning, RuntimeWarning, stacklevel=2)

        def text(self, *args, **kwargs):
            return text_impl(*args, **kwargs)

    fake_mod.DDGS = _FakeDDGS
    monkeypatch.setitem(sys.modules, "duckduckgo_search", fake_mod)


def _html_response(url="https://html.example/result"):
    html = f"""
    <html><body>
      <div class="result">
        <a class="result__a" href="{url}">HTML Result</a>
        <a class="result__snippet">HTML snippet</a>
      </div>
    </body></html>
    """

    class _Response:
        text = html

        def raise_for_status(self):
            return None

    return _Response()


def test_duckduckgo_retries_library_before_html_fallback(monkeypatch):
    calls = []
    html_calls = []

    def fake_text(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return []
        return [{
            "title": "Retry Result",
            "href": "https://example.com/retry",
            "body": "retry snippet",
        }]

    def fake_post(*args, **kwargs):
        html_calls.append((args, kwargs))
        return _html_response()

    _install_fake_ddgs(monkeypatch, fake_text)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    monkeypatch.setattr(providers, "DDG_RETRY_DELAY_SECONDS", 0, raising=False)
    monkeypatch.setattr(providers.httpx, "post", fake_post)

    results = providers.duckduckgo_search("odysseus", count=1)

    assert [r["url"] for r in results] == ["https://example.com/retry"]
    assert len(calls) == 2
    assert html_calls == []


def test_duckduckgo_suppresses_known_package_rename_warning(monkeypatch):
    def fake_text(*args, **kwargs):
        return [{
            "title": "Result",
            "href": "https://example.com/result",
            "body": "snippet",
        }]

    _install_fake_ddgs(
        monkeypatch,
        fake_text,
        constructor_warning="This package (`duckduckgo_search`) has been renamed to `ddgs`!",
    )
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        results = providers.duckduckgo_search("odysseus", count=1)

    assert [r["url"] for r in results] == ["https://example.com/result"]
    assert not [
        warning for warning in caught
        if "duckduckgo_search" in str(warning.message)
    ]


def test_duckduckgo_uses_html_fallback_after_empty_retries(monkeypatch):
    calls = []
    html_calls = []

    def fake_text(*args, **kwargs):
        calls.append((args, kwargs))
        return []

    def fake_post(*args, **kwargs):
        html_calls.append((args, kwargs))
        return _html_response("https://example.com/html")

    _install_fake_ddgs(monkeypatch, fake_text)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    monkeypatch.setattr(providers, "DDG_RETRY_DELAY_SECONDS", 0, raising=False)
    monkeypatch.setattr(providers.httpx, "post", fake_post)

    results = providers.duckduckgo_search("odysseus", count=1)

    assert [r["url"] for r in results] == ["https://example.com/html"]
    assert len(calls) == 2
    assert len(html_calls) == 1


def test_duckduckgo_still_uses_html_fallback_after_library_error(monkeypatch):
    html_calls = []

    def fake_text(*args, **kwargs):
        raise RuntimeError("ddg library down")

    def fake_post(*args, **kwargs):
        html_calls.append((args, kwargs))
        return _html_response("https://example.com/html-error")

    _install_fake_ddgs(monkeypatch, fake_text)
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {"search_safesearch": "strict"})
    monkeypatch.setattr(providers, "DDG_RETRY_DELAY_SECONDS", 0, raising=False)
    monkeypatch.setattr(providers.httpx, "post", fake_post)

    results = providers.duckduckgo_search("odysseus", count=1)

    assert [r["url"] for r in results] == ["https://example.com/html-error"]
    assert len(html_calls) == 1
