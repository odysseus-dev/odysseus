"""get_status must not rescan the whole research dir on every SSE poll.

get_avg_duration() globs and JSON-parses every file under the research data dir.
get_status() called it unconditionally on each poll, including for sessions that
are not active (the common case while a client polls a finished report). It is
now computed only for active sessions and memoized on the entry.
"""
from src.research_handler import ResearchHandler


def _handler():
    h = ResearchHandler.__new__(ResearchHandler)
    h._active_tasks = {}
    return h


def test_inactive_session_does_not_compute_avg(monkeypatch):
    h = _handler()
    calls = []
    monkeypatch.setattr(h, "get_avg_duration", lambda: (calls.append(1), 5.0)[1])
    # Unknown session, no disk file -> None, and no expensive avg scan.
    assert h.get_status("missing-session") is None
    assert calls == []


def test_active_session_memoizes_avg(monkeypatch):
    h = _handler()
    h._active_tasks["s1"] = {
        "status": "running", "progress": {}, "query": "q", "started_at": 0,
    }
    calls = []
    monkeypatch.setattr(h, "get_avg_duration", lambda: (calls.append(1), 12.0)[1])

    r1 = h.get_status("s1")
    r2 = h.get_status("s1")
    r3 = h.get_status("s1")

    assert r1["avg_duration"] == 12.0
    assert r2["avg_duration"] == 12.0 and r3["avg_duration"] == 12.0
    # Computed once across many polls, not once per poll.
    assert len(calls) == 1
