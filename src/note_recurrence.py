"""Calendar rules for reminders that must also run without an open browser."""
from calendar import monthrange
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.constants import NOTE_RECURRENCE_MAX_STEPS


@dataclass(frozen=True)
class NoteOccurrence:
    due_date: str
    repeat: str


def note_now_utc() -> datetime:
    from datetime import datetime as clock_datetime
    return clock_datetime.now(timezone.utc)


def advance_note_schedule(db, snapshot, *, after: datetime) -> NoteOccurrence | None:
    """Advance an unchanged owned schedule, shared by browser and worker paths."""
    from core.database import Note
    note_id, owner, due_date, repeat, zone = snapshot
    next_due = next_note_occurrence(due_date, repeat, after=after, timezone_name=zone)
    if next_due is None:
        return None
    changed = db.query(Note).filter(
        Note.id == note_id, Note.owner == owner, Note.archived == False,  # noqa: E712
        Note.due_date == due_date, Note.repeat == repeat, Note.reminder_timezone == zone,
    ).update({Note.due_date: next_due.due_date, Note.repeat: next_due.repeat}, synchronize_session=False)
    db.commit()
    return next_due if changed else None


def parse_note_due(value: str, timezone_name: str | None = None) -> datetime | None:
    """Resolve a saved reminder to UTC; legacy naive values use server local time."""
    try:
        due = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if due.tzinfo is None and timezone_name:
            due = due.replace(tzinfo=ZoneInfo(timezone_name))
        return due.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError, ZoneInfoNotFoundError):
        return None


def next_note_occurrence(value: str, repeat: str, *, after: datetime,
                         timezone_name: str | None = None) -> NoteOccurrence | None:
    """Advance at least once, skipping missed slots, using the saved wall clock.

    IANA zones preserve local time across DST. Legacy offset-bearing values keep
    their explicit offset; legacy naive values retain server-local semantics.
    Bare legacy rules become explicit so month-end clamping cannot change their
    anchor on the following scan.
    """
    if not repeat or repeat == "none":
        return None
    try:
        original = datetime.fromisoformat(value.replace("Z", "+00:00"))
        zone = ZoneInfo(timezone_name) if timezone_name else original.tzinfo
        local = original.astimezone(zone) if zone and original.tzinfo else original
        current = local.replace(tzinfo=None, second=0, microsecond=0)
        hh, mm = current.hour, current.minute
        wd = (current.weekday() + 1) % 7  # browser rules use Sunday=0
        rule = {
            "weekly": f"weekly:{wd}",
            "monthly": f"monthly:day:{current.day}",
            "monthly_nth_weekday": f"monthly:nth:{(current.day - 1) // 7 + 1}:{wd}",
            "monthly_last_weekday": f"monthly:last:{wd}",
        }.get(repeat, repeat)
        parts = rule.split(":")
        if rule not in {"daily", "yearly"}:
            if parts[0] == "weekly" and len(parts) == 2:
                if not 0 <= int(parts[1]) <= 6:
                    return None
            elif parts[:2] == ["monthly", "day"] and len(parts) == 3:
                if not 1 <= int(parts[2]) <= 31:
                    return None
            elif parts[:2] == ["monthly", "nth"] and len(parts) == 4:
                if not (1 <= int(parts[2]) <= 5 and 0 <= int(parts[3]) <= 6):
                    return None
            elif parts[:2] == ["monthly", "last"] and len(parts) == 3:
                if not 0 <= int(parts[2]) <= 6:
                    return None
            else:
                return None

        def aware(date):
            return date.replace(tzinfo=zone) if zone else date.astimezone()

        def normalized(date):
            # Match browser Date's forward normalization of a spring DST gap.
            instant = aware(date).astimezone(timezone.utc)
            return instant.astimezone(zone).replace(tzinfo=None)

        for _ in range(NOTE_RECURRENCE_MAX_STEPS):
            if rule == "daily":
                current += timedelta(days=1)
            elif rule == "yearly":
                # JS setFullYear carries February 29 into March 1.
                current = current.replace(year=current.year + 1, day=1) + timedelta(days=current.day - 1)
            elif parts[0] == "weekly":
                delta = (int(parts[1]) - (current.weekday() + 1) % 7) % 7 or 7
                current = (current + timedelta(days=delta)).replace(hour=hh, minute=mm)
            else:
                year = current.year + (current.month == 12)
                month = current.month % 12 + 1
                last = monthrange(year, month)[1]
                if parts[1] == "day":
                    day = min(int(parts[2]), last)
                elif parts[1] == "nth":
                    first_wd = (datetime(year, month, 1).weekday() + 1) % 7
                    day = 1 + (int(parts[3]) - first_wd) % 7 + (int(parts[2]) - 1) * 7
                    if day > last:
                        day -= 7
                else:
                    last_wd = (datetime(year, month, last).weekday() + 1) % 7
                    day = last - (last_wd - int(parts[2])) % 7
                current = current.replace(year=year, month=month, day=1).replace(day=day, hour=hh, minute=mm)
            current = normalized(current)
            if aware(current).astimezone(timezone.utc) > after.astimezone(timezone.utc):
                if "T" not in value:
                    output = current.date().isoformat()
                elif original.tzinfo and not timezone_name:
                    output = aware(current).isoformat(timespec="minutes")
                    if value.endswith("Z"):
                        output = output.replace("+00:00", "Z")
                else:
                    output = current.isoformat(timespec="minutes")
                return NoteOccurrence(output, rule)
    except (AttributeError, TypeError, ValueError, OverflowError, ZoneInfoNotFoundError):
        return None
    return None
