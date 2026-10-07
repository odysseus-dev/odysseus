"""SearXNG source results include working infoboxes and every engine group."""

import httpx
import pytest

from services.search import providers
from services.search.analytics import ProviderUnavailableError


@pytest.fixture
def search(monkeypatch):
    monkeypatch.setattr(providers, "_get_search_settings", lambda: {})
    monkeypatch.setattr(providers, "_get_search_instance", lambda: "https://search.example")
    monkeypatch.setattr(providers, "_GENERAL_ENGINES", "wikipedia,github,semantic scholar")
    calls = []

    def run(payload, count=10):
        def get(url, **kwargs):
            calls.append(kwargs)
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

        monkeypatch.setattr(providers.httpx, "get", get)
        return providers.searxng_search_api("knowledge distillation", count=count)

    run.calls = calls
    return run


def _box(**extra):
    return {
        "infobox": "Knowledge distillation", "engine": "wikipedia",
        "id": "https://en.wikipedia.org/wiki/Knowledge_distillation",
        "content": "Synthetic encyclopedia summary",
        "urls": [{"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Knowledge_distillation"}],
        **extra,
    }


def test_infobox_only_response_is_a_source_even_with_other_engine_errors(search):
    rows = search({
        "results": [], "infoboxes": [_box()],
        "unresponsive_engines": [["google", "CAPTCHA"]],
    })

    assert rows == [{
        "title": "Knowledge distillation",
        "url": "https://en.wikipedia.org/wiki/Knowledge_distillation",
        "snippet": "Synthetic encyclopedia summary",
        "provider": "searxng", "engines": ["wikipedia"],
        "published_date": None, "query": "knowledge distillation",
    }]
    assert len(search.calls) == 1


def test_plain_results_keep_order_shape_and_count_without_engine_metadata(search):
    ordinary = [{"title": str(i), "url": f"https://example.com/{i}", "content": "text"} for i in range(5)]
    rows = search({"results": ordinary}, count=3)

    assert rows == [{"title": str(i), "url": f"https://example.com/{i}", "snippet": "text",
                    "provider": "searxng", "engines": [], "published_date": None,
                    "query": "knowledge distillation"} for i in range(3)]


def test_grouped_engines_get_fair_capacity_before_result_count_limit(search):
    ordinary = []
    for engine in ["mwmbl", "github", "semantic scholar"]:
        for i in range(12):
            metadata = {"engines": [engine]} if engine == "semantic scholar" else {"engine": engine}
            ordinary.append({"title": f"{engine}-{i}", "url": f"https://example.com/{engine}/{i}", **metadata})

    rows = search({"results": ordinary}, count=10)

    assert len(rows) == 10
    assert [row["title"] for row in rows[:3]] == ["mwmbl-0", "github-0", "semantic scholar-0"]
    for engine in ["mwmbl", "github", "semantic scholar"]:
        indices = [int(row["title"].rsplit("-", 1)[1]) for row in rows if row["title"].startswith(engine + "-")]
        assert indices == list(range(len(indices)))
    assert all(set(row) == {"title", "url", "snippet", "provider", "engines", "published_date", "query"} for row in rows)


def test_shared_urls_do_not_consume_capacity_or_starve_next_engine_candidate(search):
    rows = search({"results": [
        {"engine": "github", "url": "https://example.com/shared", "title": "first"},
        {"engine": "github", "url": "https://example.com/second", "title": "second"},
        {"engine": "semantic scholar", "url": "https://example.com/shared", "title": "duplicate"},
        {"engine": "semantic scholar", "url": "https://example.com/paper", "title": "paper"},
    ], "infoboxes": [_box()]}, count=3)

    assert [row["title"] for row in rows] == ["first", "paper", "Knowledge distillation"]
    assert len({row["url"] for row in rows}) == 3


def test_infobox_url_duplicates_are_removed_and_id_fallback_is_usable(search):
    url = "https://en.wikipedia.org/wiki/Knowledge_distillation"
    rows = search({"results": [{"url": url, "title": "ordinary"}], "infoboxes": [
        _box(), _box(infobox="Other article", urls=[], id="https://en.wikipedia.org/wiki/Other"),
    ]}, count=3)

    assert [row["title"] for row in rows] == ["ordinary", "Other article"]


def test_malformed_infobox_sources_are_ignored_without_extra_fetches(search):
    rows = search({"results": [None, "invalid"], "infoboxes": [None, _box(urls=[
        None, {}, {"url": "javascript:alert(1)"}, {"url": "https://"}, {"url": "https://["},
        {"url": "https://en.wikipedia.org/wiki/Knowledge_distillation"},
    ])]})

    assert len(rows) == 1
    assert len(search.calls) == 1


def test_zero_count_remains_bounded(search):
    assert search({"results": [], "infoboxes": [_box()]}, count=0) == []


def test_unusable_results_still_report_search_engine_failures(search):
    with pytest.raises(ProviderUnavailableError, match="google: CAPTCHA"):
        search({"results": [], "infoboxes": [], "unresponsive_engines": [["google", "CAPTCHA"]]})
