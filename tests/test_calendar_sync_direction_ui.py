"""The calendar "Sync now" button must sync both ways.

It used to POST /api/calendar/sync with no direction, defaulting to pull. An
event created in Odysseus while the remote server was unreachable is left with
a pending marker, and nothing but a push retries it — so a pull-only "Sync now"
silently left local work stranded.

`direction=both` also changes the response shape from flat counts to
{push, pull} branches, which the caller has to flatten.
"""

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def source():
    return (_REPO / "static" / "js" / "calendar.js").read_text(encoding="utf-8")


def test_sync_requests_both_directions(source):
    assert "/api/calendar/sync?direction=both" in source


def test_no_pull_only_sync_call_remains(source):
    assert "`${API_BASE}/api/calendar/sync`" not in source


def test_the_branched_response_is_flattened(source):
    assert "function _flattenSyncResult(raw)" in source
    assert "if (raw && (raw.push || raw.pull)) { add(raw.push); add(raw.pull); }" in source


def test_the_flattener_sums_counts_from_both_branches(source):
    block = source.split("function _flattenSyncResult(raw)")[1].split("async function _syncCaldav")[0]
    for field in ("calendars", "events", "deleted"):
        assert f"out.{field} += Number(part.{field} || 0);" in block
    assert "out.errors.push(...part.errors);" in block


def test_errors_from_either_branch_reach_the_caller(source):
    """The button reports data.errors[0]; a push failure has to land there."""
    block = source.split("function _flattenSyncResult(raw)")[1].split("async function _syncCaldav")[0]
    assert "errors: []" in block
    assert "Array.isArray(part.errors)" in block


def test_a_push_only_change_still_refreshes_the_view(source):
    """The old test required calendars > 0, which a push-only result never
    reports, so a successful push never repainted."""
    assert "const changed = (data.events || 0) > 0 || (data.deleted || 0) > 0;" in source
