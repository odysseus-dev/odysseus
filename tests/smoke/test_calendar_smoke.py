"""Calendar: an event created through the API shows up in the range the UI asks for."""
from __future__ import annotations

from datetime import datetime, timedelta

CALENDARS_PATH = "/api/calendar/calendars"
EVENTS_PATH = "/api/calendar/events"

SUMMARY = "Odysseus smoke event"
# Far enough out that a real local calendar's own entries cannot collide
# with the assertion, and fixed relative to now so the window is never
# empty for date reasons.
DAYS_AHEAD = 30


def test_an_event_round_trips(client):
    listed_calendars = client.get(CALENDARS_PATH)
    assert listed_calendars.status_code == 200, listed_calendars.text
    assert listed_calendars.json().get("calendars"), "no calendar to write an event into"

    start = (datetime.now() + timedelta(days=DAYS_AHEAD)).replace(
        hour=10, minute=0, second=0, microsecond=0)
    created = client.post(EVENTS_PATH, json={
        "summary": SUMMARY,
        "dtstart": start.isoformat(),
    })
    assert created.status_code == 200, created.text
    uid = created.json()["uid"]
    try:
        window = client.get(EVENTS_PATH, params={
            "start": (start - timedelta(days=1)).isoformat(),
            "end": (start + timedelta(days=1)).isoformat(),
        })
        assert window.status_code == 200, window.text
        events = window.json().get("events") or []
        matching = [e for e in events if e.get("uid") == uid]
        assert matching, [e.get("summary") for e in events]
        assert matching[0].get("summary") == SUMMARY, matching[0]

        read = client.get(f"{EVENTS_PATH}/{uid}")
        assert read.status_code == 200, read.text
    finally:
        removed = client.delete(f"{EVENTS_PATH}/{uid}")
        assert removed.status_code == 200, removed.text

    after = client.get(EVENTS_PATH, params={
        "start": (start - timedelta(days=1)).isoformat(),
        "end": (start + timedelta(days=1)).isoformat(),
    })
    assert uid not in [e.get("uid") for e in after.json().get("events") or []]
