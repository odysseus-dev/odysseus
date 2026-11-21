"""CLI-created events must retain instants in the calendar's naive schema."""
import json
from datetime import datetime

import pytest

from core.database import CalendarCal, CalendarEvent
from tests.helpers.cli_loader import load_script
from tests.helpers.database import disposable_database


@pytest.fixture
def calendar_cli(tmp_path, monkeypatch):
    with disposable_database(tmp_path) as factory:
        with factory() as db:
            db.add(CalendarCal(id="calendar", name="Example", source="local"))
            db.commit()
        cli = load_script("odysseus-calendar")
        monkeypatch.setattr(cli, "SessionLocal", factory)
        yield cli, factory


@pytest.mark.parametrize("start,end,all_day,expected_start,expected_end,is_utc", [
    ("2026-12-10T10:00:00Z", "2026-12-10T11:00:00Z", False,
     "2026-12-10T10:00:00", "2026-12-10T11:00:00", True),
    ("2026-12-10T10:00:00+09:00", "2026-12-10T11:00:00+09:00", False,
     "2026-12-10T01:00:00", "2026-12-10T02:00:00", True),
    ("2026-12-10T22:30:00-07:00", "2026-12-10T23:30:00-07:00", False,
     "2026-12-11T05:30:00", "2026-12-11T06:30:00", True),
    ("2026-12-10T10:00:00+02:00", "2026-12-10T11:00:00+01:00", False,
     "2026-12-10T08:00:00", "2026-12-10T10:00:00", True),
    ("2026-12-10T00:15:00+05:30", None, False,
     "2026-12-09T18:45:00", "2026-12-09T19:45:00", True),
    ("2026-12-10T10:00:00", "2026-12-10T11:00:00", False,
     "2026-12-10T10:00:00", "2026-12-10T11:00:00", False),
    ("2026-12-10T10:00:00", None, False,
     "2026-12-10T10:00:00", "2026-12-10T11:00:00", False),
    ("2026-12-10", None, False,
     "2026-12-10T00:00:00", "2026-12-10T01:00:00", False),
    ("2026-12-10", "2026-12-11", True,
     "2026-12-10T00:00:00", "2026-12-11T00:00:00", False),
    ("2026-12-10T00:00:00+09:00", "2026-12-11T00:00:00+09:00", True,
     "2026-12-10T00:00:00", "2026-12-11T00:00:00", False),
], ids=["utc", "east", "west-rollover", "different-offsets", "default-aware-end",
        "floating", "default-floating-end", "date-only-timed", "all-day", "all-day-offset"])
def test_create_persists_and_serializes_time_semantics(
    calendar_cli, capsys, start, end, all_day, expected_start, expected_end, is_utc,
):
    cli, factory = calendar_cli
    command = ["create", "--title", "Example", "--start", start]
    if end:
        command += ["--end", end]
    if all_day:
        command += ["--all-day"]
    args = cli._build_parser().parse_args(command)

    args.func(args)
    created = json.loads(capsys.readouterr().out)

    with factory() as db:
        row = db.get(CalendarEvent, created["uid"])
        assert row.dtstart == datetime.fromisoformat(expected_start)
        assert row.dtend == datetime.fromisoformat(expected_end)
        assert row.dtstart.tzinfo is None and row.dtend.tzinfo is None
        assert row.is_utc is is_utc
    suffix = "Z" if is_utc else ""
    assert created["dtstart"] == expected_start + suffix
    assert created["dtend"] == expected_end + suffix
    assert created["all_day"] is all_day
    assert created["is_utc"] is is_utc


@pytest.mark.parametrize("start,end", [
    ("2026-12-10T10:00:00+09:00", "2026-12-10T11:00:00"),
    ("2026-12-10T10:00:00", "2026-12-10T11:00:00+09:00"),
])
def test_mixed_timed_awareness_fails_without_creating_an_event(calendar_cli, capsys, start, end):
    cli, factory = calendar_cli
    args = cli._build_parser().parse_args([
        "create", "--title", "Ambiguous", "--start", start, "--end", end,
    ])

    with pytest.raises(SystemExit) as error:
        args.func(args)

    assert error.value.code == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "both include a timezone offset or both omit it" in output.err
    with factory() as db:
        assert db.query(CalendarEvent).count() == 0
