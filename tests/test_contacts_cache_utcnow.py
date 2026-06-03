"""Regression tests for the datetime.utcnow() removal in routes/contacts_routes.py (#1116).

The contact cache ages itself out with `(_utcnow_naive() - fetched_at).total_seconds()`.
Both operands must stay naive UTC; an aware value there raises
`TypeError: can't subtract offset-naive and offset-aware datetimes`.
"""
from datetime import datetime, timedelta, timezone

import routes.contacts_routes as cr


def test_utcnow_naive_returns_naive_utc():
    now = cr._utcnow_naive()
    assert now.tzinfo is None  # must match the naive cache timestamps it subtracts
    ref = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs((ref - now).total_seconds()) < 5


def test_fresh_cache_hit_does_not_crash_on_subtraction(monkeypatch):
    # Exercise the real age-out path in _fetch_contacts (line ~288): a fresh
    # naive `fetched_at` must subtract cleanly and return the cached contacts.
    sentinel = [{"name": "cached"}]
    monkeypatch.setitem(cr._contact_cache, "fetched_at", cr._utcnow_naive())
    monkeypatch.setitem(cr._contact_cache, "contacts", sentinel)

    out = cr._fetch_contacts(force=False)  # must not raise TypeError
    assert out is sentinel


def test_aware_fetched_at_would_break_subtraction():
    # Documents why the helper must stay naive: mixing aware/naive raises.
    aware = datetime.now(timezone.utc)
    try:
        _ = (cr._utcnow_naive() - aware).total_seconds()
    except TypeError:
        return
    raise AssertionError("expected TypeError from naive/aware subtraction")
