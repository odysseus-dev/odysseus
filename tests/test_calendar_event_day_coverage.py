"""Calendar day membership uses the event's exclusive end instant."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.helpers.js_modules import js_function_source

ROOT = Path(__file__).resolve().parents[1]
SOURCE = js_function_source("_eventsForDay", (ROOT / "static/js/calendar.js").read_text())
ZONES = ["UTC", "Europe/Istanbul", "America/New_York", "Asia/Tokyo"]
CASES = [
    ("2026-10-22T17:00:00", "2026-10-23T00:00:00", False, ["2026-10-22"]),
    ("2026-10-22T17:00:00", "2026-10-23T00:01:00", False, ["2026-10-22", "2026-10-23"]),
    ("2026-10-22T17:00:00", "2026-10-22T17:00:00", False, ["2026-10-22"]),
    ("2026-10-15", "2026-10-17", True, ["2026-10-15", "2026-10-16"]),
    ("2026-10-15", "2026-10-15", True, ["2026-10-15"]),
    ("2026-10-31T23:00:00", "2026-11-01T00:00:00", False, ["2026-10-31"]),
]


def _coverage(zone, start, end, all_day, days):
    script = """
import { _localDateOf } from './static/js/calendar/utils.js';
const {source,event,days}=JSON.parse(process.argv[1]);
const filter=new Function('_events','_eventVisible','_localDateOf',source+'; return _eventsForDay;')([event],()=>true,_localDateOf);
console.log(JSON.stringify(days.filter(day=>filter(day).length)));
"""
    data = dict(source=SOURCE, event=dict(uid="event", dtstart=start, dtend=end, all_day=all_day), days=days)
    result = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(data)],
                            cwd=ROOT, env={**os.environ, "TZ": zone}, capture_output=True,
                            text=True, check=True, timeout=15)
    return json.loads(result.stdout)


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize("start,end,all_day,expected", CASES)
def test_event_day_coverage(zone, start, end, all_day, expected):
    days = [f"2026-10-{day:02d}" for day in range(14, 32)] + ["2026-11-01", "2026-11-02"]
    assert _coverage(zone, start, end, all_day, days) == expected


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("zone,start,end,days,expected", [
    ("Europe/Berlin", "2026-10-24T20:00:00+02:00", "2026-10-26T00:00:00+01:00", ["2026-10-24", "2026-10-25", "2026-10-26"], ["2026-10-24", "2026-10-25"]),
    ("America/New_York", "2026-10-31T20:00:00-04:00", "2026-11-02T00:00:00-05:00", ["2026-10-31", "2026-11-01", "2026-11-02"], ["2026-10-31", "2026-11-01"]),
])
def test_midnight_end_across_dst(zone, start, end, days, expected):
    assert _coverage(zone, start, end, False, days) == expected
