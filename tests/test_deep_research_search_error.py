"""Regression tests for deep-research search error reporting (issue #344).

When every configured search provider returns no results *without raising*
(e.g. SearXNG is reachable but all of its engines fail), ``_search`` used to
leave ``_last_search_error`` unset. The caller then surfaced a useless
"Search unavailable ... Error: unknown error" message, which is what the
reporter in #344 was confused by ("is this a model issue or deep research
issue?").

These tests pin that the empty-but-no-exception path now records an
actionable reason, while the existing raise path keeps surfacing the
provider's own error.
"""
import asyncio
import sys
import types

import pytest


@pytest.mark.parametrize(
    ("raw", "secret"),
    [
        ("api_key=sk-live-secret", "sk-live-secret"),
        ("https://api.example.test/search?key=AIzaSySecretValue&cx=test", "AIzaSySecretValue"),
        ("Authorization: Bearer bearer-secret", "bearer-secret"),
        ("Authorization: Basic basic-secret", "basic-secret"),
        ("x-api-key: header-secret", "header-secret"),
    ],
)
def test_sanitize_provider_diagnostic_redacts_common_secret_shapes(raw, secret):
    from src.deep_research import _sanitize_provider_diagnostic

    sanitized = _sanitize_provider_diagnostic(raw)

    assert secret not in sanitized
    assert "[redacted]" in sanitized


def _make_researcher():
    # Build the object without running the heavy __init__ (which wires up an
    # LLM caller etc.); _search only touches the attributes set below.
    from src.deep_research import DeepResearcher
    r = DeepResearcher.__new__(DeepResearcher)
    r.search_provider_override = None
    r.providers_used = []
    return r


def _install_search_fakes(monkeypatch, *, chain, call_provider):
    providers_mod = types.ModuleType("src.search.providers")
    providers_mod._get_search_settings = lambda: {"search_provider": chain[0]}
    core_mod = types.ModuleType("src.search.core")
    core_mod._build_provider_chain = lambda provider: list(chain)
    core_mod._call_provider = call_provider
    monkeypatch.setitem(sys.modules, "src.search.providers", providers_mod)
    monkeypatch.setitem(sys.modules, "src.search.core", core_mod)


def test_empty_results_without_exception_record_reason(monkeypatch):
    # Both providers are reachable but return nothing, and neither raises.
    _install_search_fakes(
        monkeypatch,
        chain=["searxng", "duckduckgo"],
        call_provider=lambda prov, query, n: [],
    )
    r = _make_researcher()
    results = asyncio.run(r._search("anything"))

    assert results == []
    # Before the fix this stayed unset, so the caller reported "unknown error".
    err = getattr(r, "_last_search_error", None)
    assert err, "an empty search must record a reason, not leave it unset"
    assert "no results" in err
    # Names the provider(s) that were actually tried, so the message is useful.
    assert "searxng" in err


def test_duckduckgo_empty_results_report_retry_and_html_fallback(monkeypatch):
    _install_search_fakes(
        monkeypatch,
        chain=["duckduckgo"],
        call_provider=lambda prov, query, n: [],
    )
    r = _make_researcher()
    results = asyncio.run(r._search("anything"))

    assert results == []
    err = getattr(r, "_last_search_error", None)
    assert err == "duckduckgo returned no results after retry and HTML fallback"


def test_provider_exception_is_still_surfaced(monkeypatch):
    # A provider that raises must keep surfacing its own error unchanged.
    def _boom(prov, query, n):
        raise RuntimeError("connection refused")

    _install_search_fakes(monkeypatch, chain=["searxng"], call_provider=_boom)
    r = _make_researcher()
    results = asyncio.run(r._search("anything"))

    assert results == []
    err = getattr(r, "_last_search_error", None)
    assert err and "connection refused" in err
    # The raise path, not the empty-results path.
    assert "no results" not in err


def test_provider_exception_diagnostic_redacts_key_like_values(monkeypatch):
    def _boom(prov, query, n):
        raise RuntimeError("provider rejected api_key=sk-live-secret for request")

    _install_search_fakes(monkeypatch, chain=["brave"], call_provider=_boom)
    r = _make_researcher()
    results = asyncio.run(r._search("anything"))

    assert results == []
    err = getattr(r, "_last_search_error", None)
    assert err
    assert "provider rejected" in err
    assert "sk-live-secret" not in err
    assert "[redacted]" in err


def test_provider_exception_diagnostic_redacts_url_key_parameters(monkeypatch):
    def _boom(prov, query, n):
        raise RuntimeError(
            "Google PSE failed for https://www.googleapis.com/customsearch/v1"
            "?key=AIzaSySecretValue&cx=test-cx"
        )

    _install_search_fakes(monkeypatch, chain=["google_pse"], call_provider=_boom)
    r = _make_researcher()
    results = asyncio.run(r._search("anything"))

    assert results == []
    err = getattr(r, "_last_search_error", None)
    assert err
    assert "key=[redacted]" in err
    assert "AIzaSySecretValue" not in err


def test_results_are_returned_and_provider_recorded(monkeypatch):
    # Sanity: a provider with results returns them and is recorded.
    hits = [{"url": "https://example.com", "title": "x"}]
    _install_search_fakes(
        monkeypatch, chain=["brave"], call_provider=lambda p, q, n: hits
    )
    r = _make_researcher()
    results = asyncio.run(r._search("anything"))

    assert results == hits
    assert r.providers_used == ["brave"]
