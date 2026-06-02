"""Tests for analytics default-merge on load (src/search/analytics.py)."""
import json

import src.search.analytics as analytics


def test_load_merges_defaults_for_partial_file(tmp_path, monkeypatch):
    # A file written by an older schema is missing most counters.
    f = tmp_path / "search_analytics.json"
    f.write_text(json.dumps({"total_queries": 5}), encoding="utf-8")
    monkeypatch.setattr(analytics, "ANALYTICS_FILE", f)

    data = analytics._load_analytics()

    # Existing value preserved, every missing counter filled with its default.
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
