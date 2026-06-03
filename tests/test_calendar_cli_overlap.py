"""Issue #2065 — odysseus-calendar list dropped in-progress / multi-day events.

`cmd_list` filtered on the event's START falling inside the window
(`dtstart >= start AND dtstart < end`), so an event that began before the
window but is still ongoing inside it was silently dropped — even though the
web route (routes/calendar_routes.py) uses overlap semantics and shows it.

These tests bind the CLI to a real temp SQLite DB, seed events relative to a
fixed window, and assert the overlap contract (`dtstart < end AND dtend > start`).
"""

import importlib.machinery
import importlib.util
import io
import json
import os
import uuid
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

# core.database runs init_db() on import against DATABASE_URL (default
# sqlite:///./data/app.db). Point it at an in-memory DB so the import does
# not depend on a writable ./data directory existing.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

import core.database as cdb  # noqa: E402
from core.database import CalendarCal, CalendarEvent  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# Dedicated shared in-memory SQLite for this test's data (StaticPool keeps one
# connection so every session sees the same schema/rows).
_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _load_cli(monkeypatch):
    """Load scripts/odysseus-calendar with its SessionLocal pointing at the
    temp DB. Uses the real core.database so CalendarEvent is a real model."""
    path = ROOT / "scripts" / "odysseus-calendar"
    loader = importlib.machinery.SourceFileLoader("odysseus_calendar_cli", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    monkeypatch.setattr(module, "SessionLocal", _TS)
    return module


def _make_calendar(db):
    cal = CalendarCal(id=str(uuid.uuid4()), name="Work-" + uuid.uuid4().hex[:6])
    db.add(cal)
    db.flush()
    return cal


def _add_event(db, cal, summary, dtstart, dtend):
    ev = CalendarEvent(
        uid=str(uuid.uuid4()),
        calendar_id=cal.id,
        summary=summary,
        dtstart=dtstart,
        dtend=dtend,
        is_utc=False,
    )
    db.add(ev)
    db.flush()
    return ev


def _run_list(cli, **kwargs):
    args = SimpleNamespace(
        start=kwargs.get("start"),
        end=kwargs.get("end"),
        calendar=kwargs.get("calendar"),
        limit=kwargs.get("limit", 100),
        pretty=False,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        cli.cmd_list(args)
    return json.loads(buf.getvalue())


def test_list_includes_in_progress_event(monkeypatch):
    """An event that started before the window but is still ongoing inside it
    must appear — the defining symptom of #2065."""
    cli = _load_cli(monkeypatch)
    db = _TS()
    try:
        cal = _make_calendar(db)
        # Window: 14:00–16:00. Conference runs 09:00–17:00 (started earlier,
        # still in progress). With the old dtstart >= start filter it dropped.
        _add_event(
            db, cal, "All-day conference",
            datetime(2026, 6, 1, 9, 0), datetime(2026, 6, 1, 17, 0),
        )
        db.commit()
        summaries = {
            e["summary"]
            for e in _run_list(
                cli, calendar=cal.name,
                start="2026-06-01T14:00:00", end="2026-06-01T16:00:00",
            )
        }
        assert "All-day conference" in summaries
    finally:
        db.close()


def test_list_includes_multiday_event_spanning_window(monkeypatch):
    cli = _load_cli(monkeypatch)
    db = _TS()
    try:
        cal = _make_calendar(db)
        _add_event(
            db, cal, "Trip",
            datetime(2026, 6, 1, 0, 0), datetime(2026, 6, 5, 0, 0),
        )
        db.commit()
        summaries = {
            e["summary"]
            for e in _run_list(
                cli, calendar=cal.name,
                start="2026-06-03T00:00:00", end="2026-06-04T00:00:00",
            )
        }
        assert "Trip" in summaries
    finally:
        db.close()


def test_list_excludes_non_overlapping_events(monkeypatch):
    """Events entirely before or after the window stay excluded. The end
    boundary is half-open: an event ending exactly at `start` does not overlap."""
    cli = _load_cli(monkeypatch)
    db = _TS()
    try:
        cal = _make_calendar(db)
        # Ends exactly at window start -> dtend > start is False -> excluded.
        _add_event(
            db, cal, "Before",
            datetime(2026, 6, 1, 8, 0), datetime(2026, 6, 1, 14, 0),
        )
        # Starts at window end -> dtstart < end is False -> excluded.
        _add_event(
            db, cal, "After",
            datetime(2026, 6, 1, 16, 0), datetime(2026, 6, 1, 18, 0),
        )
        _add_event(
            db, cal, "Inside",
            datetime(2026, 6, 1, 14, 30), datetime(2026, 6, 1, 15, 0),
        )
        db.commit()
        summaries = {
            e["summary"]
            for e in _run_list(
                cli, calendar=cal.name,
                start="2026-06-01T14:00:00", end="2026-06-01T16:00:00",
            )
        }
        assert summaries == {"Inside"}
    finally:
        db.close()
