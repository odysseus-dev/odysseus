"""Issue #1116 (latent ranking bug) — recency scoring uses UTC, not local time.

`recency_score` measured age with `datetime.now()` (local) against UTC-style
published dates, skewing the age by the host's UTC offset and risking a TypeError
once neighbouring code becomes timezone-aware. It now uses naive UTC and is a
module-level, time-injectable function.
"""

from datetime import datetime, timezone

import services.search.ranking as live_ranking
from services.search.ranking import recency_score, _utcnow_naive, rank_search_results


def test_fresh_result_scores_one():
    assert recency_score("2026-01-01", now=datetime(2026, 1, 5)) == 1.0  # 4 days old


def test_old_result_scores_zero():
    assert recency_score("2026-01-01", now=datetime(2026, 3, 1)) == 0.0  # >30 days


def test_mid_range_decays_linearly():
    score = recency_score("2026-01-01", now=datetime(2026, 1, 20))  # 19 days old
    assert score == (30 - 19) / 23


def test_empty_or_unparseable_scores_zero():
    assert recency_score("", now=datetime(2026, 1, 1)) == 0.0
    assert recency_score(None, now=datetime(2026, 1, 1)) == 0.0
    assert recency_score("not-a-date", now=datetime(2026, 1, 1)) == 0.0


def test_default_now_is_naive_utc():
    # Naive (no tzinfo) so it subtracts cleanly from the naive parsed dates,
    # and UTC-based (3.14-safe, no datetime.utcnow()).
    now = _utcnow_naive()
    assert now.tzinfo is None
    reference = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((now - reference).total_seconds()) < 5


def test_supported_timestamp_formats_parse():
    # All three formats the current implementation supports resolve to the same
    # ~4-day-old age, so each scores a full 1.0.
    now = datetime(2026, 1, 5, 12, 0, 0)
    assert recency_score("2026-01-01", now=now) == 1.0
    assert recency_score("2026-01-01T08:30:00", now=now) == 1.0
    assert recency_score("2026-01-01 08:30:00", now=now) == 1.0


def test_shim_reexports_live_objects():
    # src.search.ranking is a compatibility shim; it must expose the *same*
    # objects as the live services module so the two cannot diverge.
    import src.search.ranking as shim

    assert shim.recency_score is live_ranking.recency_score
    assert shim.rank_search_results is live_ranking.rank_search_results
    assert shim._utcnow_naive is live_ranking._utcnow_naive


def test_live_rank_path_prefers_newer_result(monkeypatch):
    # Pin "now" so age scoring is deterministic. The two results are identical
    # apart from age, isolating recency as the only differentiator.
    monkeypatch.setattr(live_ranking, "_utcnow_naive", lambda: datetime(2026, 1, 31))
    results = [
        {"title": "Report", "url": "https://example.org/a", "snippet": "x", "age": "2026-01-01"},
        {"title": "Report", "url": "https://example.org/b", "snippet": "x", "age": "2026-01-29"},
    ]

    ranked = rank_search_results("report", results)

    assert ranked[0]["url"] == "https://example.org/b"
    assert ranked[1]["url"] == "https://example.org/a"
