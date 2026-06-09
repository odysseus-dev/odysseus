"""CalDAV sync must not prune the window when it can't fully read the server.

The prune deletes local caldav rows whose UID the server didn't return. `seen_uids`
is built only from objects that parsed, so any parse failure (total or partial)
makes it an incomplete view of the server:

- total failure: `seen_uids` is empty and the prune falls back to `uid.isnot(None)`
  (match-all), wiping every event in the window;
- partial failure: the events that failed to parse are absent from `seen_uids`, so
  `~uid.in_(seen_uids)` deletes those still-upstream events.

`_should_prune_window` therefore only allows the prune on a clean read.
"""
from src.caldav_sync import _should_prune_window


def test_prune_runs_on_clean_read():
    # Clean read with events -> the normal ~uid.in_(seen) prune is safe.
    assert _should_prune_window({"uid-a", "uid-b"}, parse_failed=False) is True


def test_prune_runs_when_calendar_genuinely_empty():
    # Clean read, no objects -> genuinely empty window -> safe to prune.
    assert _should_prune_window(set(), parse_failed=False) is True


def test_prune_skipped_when_all_objects_failed_to_parse():
    # Every object failed -> empty seen_uids is "couldn't read", not "empty
    # calendar" -> must NOT prune (would delete the whole window).
    assert _should_prune_window(set(), parse_failed=True) is False


def test_prune_skipped_on_partial_parse_failure():
    # Some objects parsed and at least one failed: seen_uids is incomplete, so
    # pruning would delete the unparsed-but-still-upstream events. Skipping the
    # prune keeps the local copy of the unparsed event instead of deleting it.
    assert _should_prune_window({"parsed-uid"}, parse_failed=True) is False
