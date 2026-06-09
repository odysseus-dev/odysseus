"""Todo reminder due_date must be stored as an absolute UTC instant.

The UI sends naive local wall-clock times (YYYY-MM-DDTHH:MM). The background
note scanner (action_ping_notes) compares against UTC now; treating naive
strings as server-local breaks when Docker runs in UTC but the user is not.
"""
from datetime import datetime, timezone

import pytest

from routes.note_routes import _normalize_due_date
from src.user_time import clear_user_time_context


class _FakeRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _clear_tz():
    clear_user_time_context()
    yield
    clear_user_time_context()


def test_naive_local_with_user_offset_stored_as_utc_z():
    # Browser in UTC-4 sends 18:00 local with offset header.
    req = _FakeRequest({"x-tz-offset": "-240"})
    stored = _normalize_due_date(req, "2026-06-09T18:00")
    assert stored.endswith("Z")
    parsed = datetime.fromisoformat(stored.replace("Z", "+00:00"))
    assert parsed == datetime(2026, 6, 9, 22, 0, tzinfo=timezone.utc)


def test_z_suffix_round_trips():
    req = _FakeRequest()
    iso = "2026-06-09T22:00:00.000Z"
    stored = _normalize_due_date(req, iso)
    assert stored.endswith("Z")
    assert datetime.fromisoformat(stored.replace("Z", "+00:00")) == datetime(
        2026, 6, 9, 22, 0, tzinfo=timezone.utc
    )


def test_offset_iso_normalized_to_z():
    req = _FakeRequest()
    stored = _normalize_due_date(req, "2026-06-09T18:00:00-04:00")
    assert stored == "2026-06-09T22:00:00+00:00".replace("+00:00", "Z")


def test_builtin_parse_due_handles_stored_z():
    from src.user_time import parse_stored_due_utc

    iso = "2026-06-09T22:00:00.000Z"
    assert parse_stored_due_utc(iso) == datetime(2026, 6, 9, 22, 0, tzinfo=timezone.utc)
