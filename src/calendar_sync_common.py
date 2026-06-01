"""Shared helpers for one-way calendar sync (remote → local SQLite).

The CalDAV sync (`src/caldav_sync.py`) was the first pull integration and
grew its own copies of these helpers. The Google Calendar (`src/gcal_sync.py`)
and Calendly (`src/calendly_sync.py`) pulls follow the exact same shape — a
remote source maps to one local `CalendarCal` row keyed by a stable hash, and
events upsert by UID with stale local rows pruned inside the sync window — so
that logic lives here once instead of three times.

Design notes (mirrors the CalDAV module's contract so all three behave the
same to the rest of the app):
- Each remote source maps to one local `CalendarCal` whose `id` is a stable
  hash of a source key (feed URL, Calendly user URI, …) so re-syncs are
  idempotent and target the same row across restarts.
- Events upsert by UID. Local rows for that calendar not seen in the latest
  pull — but inside the sync window — are deleted so remote deletions
  propagate without false-deleting far-future events outside the window.
- Datetimes are normalised to naive-UTC and flagged `is_utc=True` so the
  serializer adds the `Z` suffix and the frontend renders in the user's local
  TZ. All-day events stay date-only with `is_utc=False`.
"""

import hashlib
from datetime import date, datetime, timedelta, timezone

# Pull window shared by every source: 90 days back, 1 year forward. Keeps the
# remote query cheap and matches what the calendar UI typically renders. Far-
# future recurring events still come through via RRULE expansion on the
# frontend.
LOOKBACK_DAYS = 90
LOOKAHEAD_DAYS = 365


def window():
    """The (start, end) datetime pair every pull restricts itself to."""
    now = datetime.utcnow()
    return now - timedelta(days=LOOKBACK_DAYS), now + timedelta(days=LOOKAHEAD_DAYS)


def stable_cal_id(prefix: str, key: str) -> str:
    """Deterministic local id for a remote calendar — the same source key
    always maps to the same local row across restarts and re-syncs.

    `prefix` namespaces the source ("gcal", "calendly", …) so two providers
    that happen to share a key string never collide.
    """
    h = hashlib.sha256(f"{prefix}:{key}".encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{h}"


def to_utc_naive(dt):
    """Normalise a parsed dtstart/dtend to ``(naive_datetime, all_day)``.

    Datetimes may be tz-aware (carry an offset) or naive; dates are all-day.
    The DB column is naive, so tz-aware values convert to UTC and lose tzinfo;
    naive values are treated as already-local. All-day dates widen to a
    midnight datetime. Returns the value plus an all-day flag.
    """
    if isinstance(dt, datetime):
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None), False
        return dt, False  # naive → treat as local
    # date-only (all-day)
    return datetime(dt.year, dt.month, dt.day), True


def get_or_create_cal(db, owner: str, cal_id: str, display_name: str,
                      color: str, source: str):
    """Fetch the local `CalendarCal` for this source or create it. Refreshes
    the display name if the user renamed it remotely; preserves any local
    color override on an existing row."""
    from core.database import CalendarCal

    local_cal = db.query(CalendarCal).filter(
        CalendarCal.id == cal_id,
        CalendarCal.owner == owner,
    ).first()
    if not local_cal:
        local_cal = CalendarCal(
            id=cal_id,
            owner=owner,
            name=display_name,
            color=color,
            source=source,
        )
        db.add(local_cal)
        db.commit()
    elif local_cal.name != display_name and display_name:
        local_cal.name = display_name
        db.commit()
    return local_cal


def upsert_event(db, calendar_id: str, fields: dict):
    """Insert or update a single event by UID within `calendar_id`.

    `fields` must contain: uid, summary, description, location, dtstart,
    dtend, all_day, is_utc, rrule. The UID is the natural key (same as the
    CalDAV path), so an event that moves calendars or changes time updates in
    place rather than duplicating.
    """
    from core.database import CalendarEvent

    uid_val = fields["uid"]
    existing = db.query(CalendarEvent).filter(
        CalendarEvent.uid == uid_val,
    ).first()
    if existing:
        existing.calendar_id = calendar_id
        existing.summary = fields["summary"]
        existing.description = fields["description"]
        existing.location = fields["location"]
        existing.dtstart = fields["dtstart"]
        existing.dtend = fields["dtend"]
        existing.all_day = fields["all_day"]
        existing.is_utc = fields["is_utc"]
        existing.rrule = fields.get("rrule", "")
    else:
        db.add(CalendarEvent(
            uid=uid_val,
            calendar_id=calendar_id,
            summary=fields["summary"],
            description=fields["description"],
            location=fields["location"],
            dtstart=fields["dtstart"],
            dtend=fields["dtend"],
            all_day=fields["all_day"],
            is_utc=fields["is_utc"],
            rrule=fields.get("rrule", ""),
        ))


def prune_stale(db, calendar_id: str, start, end, seen_uids) -> int:
    """Delete locally-cached events for `calendar_id` that vanished upstream.

    Only prunes within the sync window — events outside `[start, end]` aren't
    in the latest pull, so deleting them would be a false-positive. Returns the
    number of rows removed.
    """
    from core.database import CalendarEvent

    stale = db.query(CalendarEvent).filter(
        CalendarEvent.calendar_id == calendar_id,
        CalendarEvent.dtstart >= start,
        CalendarEvent.dtstart <= end,
        ~CalendarEvent.uid.in_(seen_uids) if seen_uids else CalendarEvent.uid.isnot(None),
    ).all()
    for ev in stale:
        db.delete(ev)
    return len(stale)


def empty_result(*errors) -> dict:
    """A zero-count result dict, optionally carrying error strings. Matches the
    shape every sync function returns: {calendars, events, deleted, errors}."""
    return {"calendars": 0, "events": 0, "deleted": 0, "errors": list(errors)}
