"""Behavioral coverage for the week view's deployed timed-event layout."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not installed")


def _layout(intervals, *, grid_start=0, grid_end=1440, hour_px=60):
    script = """
        import { layoutTimedEvents } from './static/js/features/calendar/domain/weekLayout.js';
        const { intervals, grid } = JSON.parse(process.argv[1]);
        const events = intervals.map(([uid, startMinute, endMinute]) => ({
          ev: { uid }, startMinute, endMinute,
        }));
        const items = layoutTimedEvents(events, grid);
        console.log(JSON.stringify(items.map(({ ev, top, height, lane, laneCount }) =>
          [ev.uid, top, height, lane, laneCount])));
    """
    data = {
        "intervals": intervals,
        "grid": {"gridStart": grid_start, "gridEnd": grid_end, "hourPx": hour_px},
    }
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(data)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return json.loads(result.stdout)


def test_week_layout_reuses_lanes_within_an_overlap_group():
    assert _layout([
        ("adjacent", 660, 720),
        ("third", 600, 660),
        ("second", 570, 630),
        ("first", 540, 600),
    ]) == [
        ["first", 540, 60, 0, 2],
        ["second", 570, 60, 1, 2],
        ["third", 600, 60, 0, 2],
        ["adjacent", 660, 60, 0, 1],
    ]


def test_week_layout_clips_to_grid_and_preserves_duration_defaults():
    assert _layout([
        ("before-grid", 0, 90),
        ("short", 300, 305),
        ("missing-end", 600, None),
        ("after-grid", 1320, 1440),
    ], grid_start=60, grid_end=1380) == [
        ["before-grid", 0, 30, 0, 1],
        ["short", 240, 18, 0, 1],
        ["missing-end", 540, 60, 0, 1],
        ["after-grid", 1260, 60, 0, 1],
    ]


def test_week_layout_assigns_lanes_when_zoomed_out_blocks_visually_overlap():
    assert _layout([
        ("first", 0, 15),
        ("second", 15, 30),
    ], hour_px=28) == [
        ["first", 0, 18, 0, 2],
        ["second", 7, 18, 1, 2],
    ]
