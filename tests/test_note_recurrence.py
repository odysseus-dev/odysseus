import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from src.note_recurrence import next_note_occurrence, parse_note_due


@pytest.mark.parametrize("zone", ["UTC", "Europe/Istanbul", "Europe/Berlin", "America/New_York"])
def test_calendar_rules_match_existing_browser(zone):
    examples = [
        ("2026-10-28T12:30", "daily"),
        ("2026-10-28T12:30", "weekly"),
        ("2026-10-28T12:30", "weekly:0"),
        ("2026-01-31T08:00", "monthly"),
        ("2026-01-31T08:00", "monthly:day:31"),
        ("2026-01-31T08:00", "monthly_nth_weekday"),
        ("2026-01-31T08:00", "monthly_last_weekday"),
        ("2026-01-31T08:00", "monthly:nth:5:6"),
        ("2026-01-31T08:00", "monthly:last:0"),
        ("2024-02-29T12:00", "yearly"),
        ("2026-03-07T02:30", "daily"),
        ("2026-10-31T01:30", "daily"),
    ]
    cases = [{"value": value, "repeat": repeat,
              "after": (parse_note_due(value, zone) + timedelta(seconds=30)).isoformat()}
             for value, repeat in examples]
    cases.append({"value": "2026-03-01T02:30", "repeat": "daily", "after": "2026-03-20T12:00:00Z"})
    module = Path(__file__).resolve().parents[1] / "static/js/features/notes/domain/recurrence.js"
    script = """
        import fs from 'node:fs';
        const source = fs.readFileSync(process.argv[1], 'utf8');
        const { _advanceRecurring } = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
        const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
        console.log(JSON.stringify(cases.map(c => _advanceRecurring(c.value, c.repeat, {now:()=>Date.parse(c.after)}))));
    """
    result = subprocess.run(["node", "--input-type=module", "-e", script, str(module)],
                            input=json.dumps(cases), text=True, capture_output=True,
                            env={**os.environ, "TZ": zone}, check=True)
    expected = json.loads(result.stdout)
    actual = [next_note_occurrence(c["value"], c["repeat"],
              after=datetime.fromisoformat(c["after"].replace("Z", "+00:00")), timezone_name=zone).due_date
              for c in cases]
    assert actual == expected


def test_legacy_month_end_anchor_survives_the_next_scan():
    first = next_note_occurrence("2026-01-31T08:00", "monthly",
                                after=datetime(2026, 1, 31, 9, tzinfo=timezone.utc), timezone_name="UTC")
    assert (first.due_date, first.repeat) == ("2026-02-28T08:00", "monthly:day:31")
    second = next_note_occurrence(first.due_date, first.repeat,
                                 after=datetime(2026, 2, 28, 9, tzinfo=timezone.utc), timezone_name="UTC")
    assert second.due_date == "2026-03-31T08:00"


def test_known_zone_preserves_wall_time_across_dst():
    next_due = next_note_occurrence("2026-03-07T09:00", "daily",
        after=datetime(2026, 3, 7, 14, 1, tzinfo=timezone.utc), timezone_name="America/New_York")
    assert next_due.due_date == "2026-03-08T09:00"
    assert parse_note_due(next_due.due_date, "America/New_York") == datetime(2026, 3, 8, 13, tzinfo=timezone.utc)


def test_legacy_explicit_offset_is_preserved_without_guessing_a_zone():
    next_due = next_note_occurrence("2026-10-28T12:00+03:00", "daily",
                                  after=datetime(2026, 10, 28, 9, 1, tzinfo=timezone.utc))
    assert next_due.due_date == "2026-10-29T12:00+03:00"


@pytest.mark.parametrize("value,repeat,zone", [
    ("bad", "daily", "UTC"), ("2026-01-01T09:00", "none", "UTC"),
    ("2026-01-01T09:00", "weekly:7", "UTC"), ("2026-01-01T09:00", "monthly:day:0", "UTC"),
    ("2026-01-01T09:00", "monthly:nth:9:0", "UTC"),
    ("2026-01-01T09:00", "monthly:last:9", "UTC"),
    ("2026-01-01T09:00", "unknown", "UTC"), ("2026-01-01T09:00", "daily", "Invalid/Zone"),
])
def test_invalid_rules_do_not_advance(value, repeat, zone):
    assert next_note_occurrence(value, repeat, after=datetime(2026, 1, 1, tzinfo=timezone.utc), timezone_name=zone) is None
