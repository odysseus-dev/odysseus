"""Regression: `odysseus-calendar list` must select events that OVERLAP the
query window, matching the canonical web-route filter in
routes/calendar_routes.py (`dtstart < end AND dtend > start`) and the
recurring-expansion contract asserted in test_calendar_recurrence.py
(test_expand_multi_day_crossing_range_start).

The buggy CLI filtered on `dtstart >= start AND dtstart < end`, which drops a
multi-day / in-progress event that started before the window but is still
running inside it (e.g. an all-day-running conference when you call
`odysseus-calendar list` with the default start=now()).
"""

import importlib.machinery
import importlib.util
import sys
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]


class _Col:
    """A fake SQLAlchemy column that records comparison clauses instead of
    building SQL. `Col >= x` / `Col < x` / `Col > x` evaluate against a row
    later via .matches(row)."""

    def __init__(self, name):
        self.name = name

    def __ge__(self, other):
        return _Clause(self.name, ">=", other)

    def __lt__(self, other):
        return _Clause(self.name, "<", other)

    def __gt__(self, other):
        return _Clause(self.name, ">", other)

    # asc()/order_by helpers used by cmd_list — return self, harmless.
    def asc(self):
        return self


class _Clause:
    def __init__(self, col, op, value):
        self.col = col
        self.op = op
        self.value = value

    def matches(self, row):
        actual = getattr(row, self.col)
        if self.op == ">=":
            return actual >= self.value
        if self.op == "<":
            return actual < self.value
        if self.op == ">":
            return actual > self.value
        raise AssertionError(self.op)


class _Query:
    def __init__(self, rows):
        self.rows = rows
        self.clauses = []

    def filter(self, *conds):
        self.clauses.extend(conds)
        return self

    def order_by(self, *a, **k):
        return self

    def limit(self, n):
        return self

    def first(self):
        return None

    def all(self):
        out = []
        for r in self.rows:
            if all(c.matches(r) for c in self.clauses if isinstance(c, _Clause)):
                out.append(r)
        return out


def _load_cli(monkeypatch, rows):
    db = types.ModuleType("core.database")
    session = MagicMock()
    session.query.return_value = _Query(rows)
    db.SessionLocal = MagicMock(return_value=session)
    cal_event = types.SimpleNamespace(dtstart=_Col("dtstart"), dtend=_Col("dtend"))
    db.CalendarEvent = cal_event
    db.CalendarCal = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db)
    path = ROOT / "scripts" / "odysseus-calendar"
    loader = importlib.machinery.SourceFileLoader("odysseus_calendar_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_list_includes_event_overlapping_window_start(monkeypatch, capsys):
    # Conference running 09:00–17:00; we list from 14:00 onward (default now()).
    ongoing = types.SimpleNamespace(
        dtstart=datetime(2026, 6, 3, 9, 0),
        dtend=datetime(2026, 6, 3, 17, 0),
    )
    cli = _load_cli(monkeypatch, [ongoing])

    # Serialize to something trivial so emit() doesn't choke on the namespace.
    cli._serialize_event = lambda e: {"dtstart": e.dtstart.isoformat()}

    args = types.SimpleNamespace(
        start="2026-06-03T14:00:00",
        end="2026-06-03T23:00:00",
        calendar=None,
        limit=100,
        pretty=False,
    )
    cli.cmd_list(args)
    out = capsys.readouterr().out
    assert "2026-06-03T09:00:00" in out, (
        "An event that started before the window but is still running inside "
        "it must be listed (overlap semantics), but it was dropped."
    )
