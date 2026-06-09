"""Search providers must not raise on a non-JSON response body (issue #1129).

`brave_search` already wraps `response.json()` in its own try/except that catches
`json.JSONDecodeError` and returns []. The Tavily, Serper, and Google PSE
providers parsed JSON inside the network try block, which only caught
`httpx.RequestError`/`RateLimitError` — so a provider returning a non-JSON body
(an HTML error page, a truncated/empty body, a gateway error) raised an
UNCAUGHT `json.JSONDecodeError` that aborted the search in the background. These
pin that all four providers degrade to [] on malformed JSON, matching brave.
"""

import json

import pytest

from services.search import providers


class _BadJSONResponse:
    """A 200 response whose body is not valid JSON (e.g. an HTML error page)."""
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        raise json.JSONDecodeError("Expecting value", "<html>down</html>", 0)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    # Keep everything offline + deterministic: no settings/DB, keys via env, and
    # both httpx verbs return a body that fails to decode.
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {}, raising=False)
    monkeypatch.setattr(providers, "_safesearch_for", lambda *_a, **_k: None, raising=False)
    monkeypatch.setenv("DATA_BRAVE_API_KEY", "k")
    monkeypatch.setenv("TAVILY_API_KEY", "k")
    monkeypatch.setenv("SERPER_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_PSE_CX", "cx")
    monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: _BadJSONResponse())
    monkeypatch.setattr(providers.httpx, "get", lambda *a, **k: _BadJSONResponse())


def test_tavily_malformed_json_returns_empty():
    assert providers.tavily_search("hello") == []


def test_serper_malformed_json_returns_empty():
    assert providers.serper_search("hello") == []


def test_google_pse_malformed_json_returns_empty():
    assert providers.google_pse_search("hello") == []


def test_brave_malformed_json_returns_empty():
    # Already correct on main — guards against regressing the reference behaviour.
    assert providers.brave_search("hello") == []
