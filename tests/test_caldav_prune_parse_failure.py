"""CalDAV sync must not prune the whole window when it can't read the server.

The prune deletes local caldav rows whose UID the server didn't return. When
`seen_uids` is empty it falls back to `uid.isnot(None)` (match-all). That is
correct when the calendar is genuinely empty, but catastrophic when the server
returned objects and every one failed to parse — `seen_uids` is empty only
because nothing could be read, so the match-all branch wipes every event in the
window. `_should_prune_window` gates the prune on that distinction.
"""
from src.caldav_sync import _should_prune_window


def test_prune_runs_when_calendar_genuinely_empty():
    # No objects returned, no parse errors -> server really has nothing -> prune.
    assert _should_prune_window(set(), parse_failed=False) is True


def test_prune_skipped_when_all_objects_failed_to_parse():
    # Objects came back but none parsed -> empty seen_uids is not "empty
    # calendar" -> must NOT prune (would delete the whole window).
    assert _should_prune_window(set(), parse_failed=True) is False


def test_prune_runs_when_some_uids_seen_even_with_parse_errors():
    # At least one event was read -> the normal ~uid.in_(seen) prune is safe.
    assert _should_prune_window({"uid-a"}, parse_failed=True) is True
    assert _should_prune_window({"uid-a"}, parse_failed=False) is True
