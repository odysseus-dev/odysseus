"""[CONTENT i] blocks must map to the [i] sources list.

comprehensive_web_search numbers its sources list by search-result order,
but the fetched-content blocks were numbered 1..N in fetch COMPLETION
order (as_completed). With parallel fetching the two numberings disagree,
so the model cites "[2]" for content that actually came from source [3].
"""

import importlib
import time

import pytest


@pytest.fixture
def core(monkeypatch):
    mod = importlib.import_module("services.search.core")
    results = [
        {"url": "http://one.example/a", "title": "One", "snippet": "s1"},
        {"url": "http://two.example/b", "title": "Two", "snippet": "s2"},
    ]
    monkeypatch.setattr(mod, "_get_search_settings", lambda: {"search_provider": "searxng"})
    monkeypatch.setattr(mod, "_get_result_count", lambda: 2)
    monkeypatch.setattr(mod, "_call_provider", lambda *a, **k: [dict(r) for r in results])
    monkeypatch.setattr(mod, "rank_search_results", lambda q, r: r)
    return mod


def _fake_fetch_delaying_first(url, timeout=8, retry_attempt=0):
    if "one.example" in url:
        # Force the FIRST source to finish fetching LAST
        time.sleep(0.4)
    return {
        "success": True,
        "url": url,
        "title": "Title for " + url,
        "content": "Content for " + url + " " + "filler " * 20,
    }


def test_content_blocks_numbered_by_source_not_completion_order(core, monkeypatch):
    monkeypatch.setattr(core, "fetch_webpage_content", _fake_fetch_delaying_first)
    out = core.comprehensive_web_search("test query", max_pages=2, max_workers=2)
    assert "[CONTENT 1] From: http://one.example/a" in out
    assert "[CONTENT 2] From: http://two.example/b" in out
    assert out.index("[CONTENT 1]") < out.index("[CONTENT 2]")


def test_redirected_fetch_keeps_its_source_index(core, monkeypatch):
    def fetch(url, timeout=8, retry_attempt=0):
        final = "http://final.example/landing" if "two.example" in url else url
        return {
            "success": True,
            "url": final,
            "title": "Title",
            "content": "Content for " + final + " " + "filler " * 20,
        }

    monkeypatch.setattr(core, "fetch_webpage_content", fetch)
    out = core.comprehensive_web_search("test query", max_pages=2, max_workers=2)
    assert "[CONTENT 2] From: http://final.example/landing" in out
