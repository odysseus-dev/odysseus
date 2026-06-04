"""Behavioral guard for the cookbook error output-tail expansion.

When a task reaches status "error" the status endpoint previously returned
only the last 12 lines of the subprocess log. The "Copy last 50 lines"
context-menu action was therefore copying the same 12 lines — useless for
diagnosing failures that emit long stack traces or build output.

`error_aware_output_tail` now returns the last 50 lines on error and keeps
the cheaper 12-line tail for running/other tasks.
"""
from routes.cookbook_output import error_aware_output_tail


def _snapshot(n):
    return "\n".join(f"line {i}" for i in range(n))


def test_error_status_returns_last_50_lines():
    snap = _snapshot(200)
    tail = error_aware_output_tail(snap, "error")
    lines = tail.splitlines()
    assert len(lines) == 50, f"error tail should be 50 lines, got {len(lines)}"
    assert lines[0] == "line 150"
    assert lines[-1] == "line 199"


def test_non_error_status_returns_last_12_lines():
    snap = _snapshot(200)
    for status in ("running", "ready", "completed", "stopped", "unknown"):
        tail = error_aware_output_tail(snap, status)
        lines = tail.splitlines()
        assert len(lines) == 12, f"{status} tail should be 12 lines, got {len(lines)}"
        assert lines[-1] == "line 199"


def test_short_snapshot_returns_all_lines():
    # Fewer lines than the cap — return everything, no padding.
    snap = _snapshot(5)
    assert error_aware_output_tail(snap, "error").splitlines() == [
        "line 0", "line 1", "line 2", "line 3", "line 4",
    ]
    assert len(error_aware_output_tail(snap, "running").splitlines()) == 5


def test_empty_snapshot_returns_empty_string():
    assert error_aware_output_tail("", "error") == ""
    assert error_aware_output_tail("", "running") == ""


def test_error_tail_is_wider_than_non_error():
    snap = _snapshot(100)
    err = error_aware_output_tail(snap, "error").splitlines()
    run = error_aware_output_tail(snap, "running").splitlines()
    assert len(err) > len(run)
    # The non-error tail is a strict suffix of the error tail.
    assert err[-len(run):] == run
