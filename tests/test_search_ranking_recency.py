"""Issue #1116 (latent ranking bug) — recency scoring uses UTC, not local time.

`recency_score` measured age with `datetime.now()` (local) against UTC-style
published dates, skewing the age by the host's UTC offset and risking a TypeError
once neighbouring code becomes timezone-aware. It now uses naive UTC and is a
module-level, time-injectable function.
"""

from datetime import datetime, timezone

from src.search.ranking import recency_score, _utcnow_naive


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
