"""Default-merge on load for services/search/analytics.py.

src/search/analytics.py was fixed to merge a loaded analytics file over
defaults so _record_query never hits a missing counter, but the services
copy diverged and still returns json.load(f) verbatim. The services copy
is the live one: services/search/core.py calls _record_query on every
search, so an analytics file missing a key (older schema or partial
write) raises KeyError and breaks comprehensive_web_search.

Mirrors tests/test_search_analytics_defaults.py which covers the src copy.
"""
import json

import services.search.analytics as analytics


def test_load_merges_defaults_for_partial_file(tmp_path, monkeypatch):
    f = tmp_path / "search_analytics.json"
    f.write_text(json.dumps({"total_queries": 5}), encoding="utf-8")
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", f)

    data = analytics._load_analytics()

    assert data["total_queries"] == 5
    assert data["query_patterns"] == {}
    for key in ("successful_queries", "failed_queries", "cache_hits", "cache_misses"):
        assert data[key] == 0


def test_record_query_survives_partial_file(tmp_path, monkeypatch):
    f = tmp_path / "search_analytics.json"
    f.write_text(json.dumps({"total_queries": 1}), encoding="utf-8")
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", f)

    # Before the fix this raised KeyError on the missing counters.
    analytics._record_query("hello world", success=True, cache_hit=False)

    data = analytics._load_analytics()
    assert data["total_queries"] == 2
    assert data["successful_queries"] == 1
    assert data["query_patterns"]["hello world"]["count"] == 1
