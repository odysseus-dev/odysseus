"""Calendar-domain tool implementations.

Extracted from tool_implementations.py as part of slice 1 (#4082/#4071).
Holds the manage_calendar tool (CalDAV-backed event CRUD).
``src.tool_implementations`` re-exports these for backward compatibility.
"""
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Dict, Optional

from src.tools._common import _parse_tool_args
from src.tool_utils import get_upload_handler
from src.upload_handler import reserve_upload_references

logger = logging.getLogger(__name__)


def _normalize_local_event_times(args: dict) -> dict:
    args = dict(args)
    for field, target in (('local_start', 'dtstart'), ('local_end', 'dtend')):
        if field not in args:
            continue
        value = args[field]
        if not isinstance(value, dict):
            raise ValueError(f'{field} must contain date and time fields')
        day, clock = value.get('date'), value.get('time')
        if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError(f'{field}.date must be YYYY-MM-DD')
        if args.get('all_day') is True:
            if clock:
                raise ValueError(f'Omit {field}.time for an all-day event')
            normalized = day
        else:
            if not isinstance(clock, str) or not re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?', clock):
                raise ValueError(f'{field}.time must be HH:MM or HH:MM:SS; put its zone in timezone')
            normalized = day + 'T' + clock
        parsed = datetime.fromisoformat(normalized)
        if target in args and datetime.fromisoformat(str(args[target])) != parsed:
            raise ValueError(f'Conflicting {field} and {target}; use only one representation')
        args[target] = normalized
    return args


def _saved_event_times(event) -> dict:
    """Report persisted timestamps, not the model's unnormalized input."""
    def serialize(value):
        if value is None:
            return None
        if event.all_day:
            return value.date().isoformat()
        return value.isoformat() + ('Z' if event.is_utc else '')

    return {
        'dtstart': serialize(event.dtstart),
        'dtend': serialize(event.dtend),
        'all_day': bool(event.all_day),
        'is_utc': bool(event.is_utc),
    }


def _explicit_calendar_time(raw: str, zone_name: str) -> tuple[datetime, bool]:
    """Convert a stated wall time without relying on the browser timezone."""
    zone_name = str(zone_name).strip()
    offset = re.fullmatch(r'(?:UTC|GMT)?([+-])(\d{2}):(\d{2})', zone_name, re.I)
    if zone_name.upper() in {'UTC', 'GMT', 'Z'}:
        zone = timezone.utc
    elif offset:
        hours, minutes = int(offset[2]), int(offset[3])
        if hours > 23 or minutes > 59:
            raise ValueError('Invalid timezone offset')
        zone = timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if offset[1] == '+' else -1)))
    else:
        try:
            zone = ZoneInfo(zone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError('timezone must be UTC, a signed HH:MM offset, or an IANA zone') from exc
    value = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    if value.tzinfo is not None:
        if value.utcoffset() != value.astimezone(zone).utcoffset():
            raise ValueError('Timestamp offset conflicts with timezone; preserve the stated wall time and zone')
        return value.astimezone(timezone.utc).replace(tzinfo=None), True
    candidates = set()
    for fold in (0, 1):
        instant = value.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None) == value:
            candidates.add(instant)
    if len(candidates) != 1:
        raise ValueError('Local time is ambiguous or nonexistent due to daylight saving; specify a valid time with explicit offset')
    return candidates.pop().replace(tzinfo=None), True


async def do_manage_calendar(content: str, owner: Optional[str] = None, *, import_event_uid: Optional[str] = None) -> Dict:
    """Handle manage_calendar tool calls: list/create/update/delete calendar events (local SQLite)."""
    from core.database import SessionLocal, CalendarCal, CalendarEvent, Note
    from routes.calendar_routes import (
        _ensure_default_calendar,
        _parse_dt,
        _parse_dt_pair,
        parse_due_for_user,
        _resolve_base_uid,
        _push_caldav_event_after_commit,
        _record_caldav_delete_tombstone,
        _delete_calendar_reminders_for_event,
        _calendar_reminder_for_event,
        _event_to_dict,
    )
    import uuid as _uuid

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}
    try:
        args = _normalize_local_event_times(args)
    except (ValueError, TypeError) as exc:
        return {"error": str(exc), "exit_code": 1}

    # ── Batch normalization ──
    # Some models (e.g. deepseek-v4-flash) emit {"events": [{...}, ...]}
    # instead of individual create_event calls. Iterate and create each.
    if isinstance(args.get("events"), list) and not args.get("action"):
        results = []
        for ev in args["events"]:
            if not isinstance(ev, dict):
                continue
            # Normalize start/end from {dateTime: "..."} object to flat string
            for field, target in [("start", "dtstart"), ("end", "dtend")]:
                val = ev.pop(field, None)
                if val and target not in ev:
                    ev[target] = val.get("dateTime", val) if isinstance(val, dict) else val
            ev.setdefault("action", "create_event")
            r = await do_manage_calendar(json.dumps(ev), owner=owner)
            results.append(r)
        created = [r for r in results if r.get("exit_code") == 0 and not r.get("error")]
        failed = [r for r in results if r.get("error")]

        if not results:
            return {"error": "No events to create", "exit_code": 1}

        # Surface both successes and failures
        parts = []
        if created:
            summaries = [r.get("response", "") for r in created]
            parts.append(f"Created {len(created)} event(s):\n" + "\n".join(summaries))
        if failed:
            first_error = failed[0].get("error", "Unknown error")
            parts.append(f"Failed to create {len(failed)} event(s). First error: {first_error}")

        response = "\n\n".join(parts)
        # Non-zero exit code for partial or total failure
        exit_code = 0 if not failed else 1
        return {"response": response, "exit_code": exit_code, "created_count": len(created), "failed_count": len(failed)}

    # Normalize action — some models emit hyphens ("list-calendars") instead
    # of underscores. Treat them as equivalent so we don't bounce a
    # cosmetic typo back to the model and waste a round-trip. Also accept
    # short forms (`create`, `update`, `delete`) as aliases for the
    # full `<verb>_event` names — models keep emitting the short forms.
    action = (args.get("action") or "list_events").replace("-", "_").strip().lower()
    _ACTION_ALIASES = {
        "create": "create_event",
        "update": "update_event",
        "delete": "delete_event",
        "list": "list_events",
    }
    action = _ACTION_ALIASES.get(action, action)
    db = SessionLocal()

    def _calendar_query():
        q = db.query(CalendarCal)
        if owner is not None:
            q = q.filter(CalendarCal.owner == owner)
        return q

    def _event_query():
        q = db.query(CalendarEvent).join(CalendarCal)
        if owner is not None:
            q = q.filter(CalendarCal.owner == owner)
        return q

    def _event_uid_candidates(raw_uid):
        """Yield exact UID first, then unambiguous UI-anchor spellings.

        Calendar results render links as ``#event-<uid>``. Models sometimes
        copy that href (or drop only the leading ``#``) into the UID field.
        Preserve real UIDs beginning with ``event-`` by trying the exact value
        first and using the stripped form only as a not-found fallback.
        """
        text = str(raw_uid or "").strip()
        candidates = [text]
        if text.startswith("#event-"):
            candidates.append(text[len("#event-"):])
        elif text.startswith("event-"):
            candidates.append(text[len("event-"):])
        return [item for index, item in enumerate(candidates) if item and item not in candidates[:index]]

    def _first_present_arg(raw_args, *names: str):
        for name in names:
            if name in raw_args and raw_args.get(name) is not None:
                return raw_args.get(name)
        return None

    def _has_reminder_request(raw_args) -> bool:
        if any(name in raw_args for name in (
            "reminder_minutes",
            "remind_before_minutes",
            "alarm_minutes",
            "reminder",
            "alarm",
        )):
            return True
        return bool(re.search(r"\b(remind|reminder|alarm)\b", str(raw_args.get("description") or ""), re.I))

    def _reminder_minutes(raw_args) -> Optional[int]:
        raw = _first_present_arg(
            raw_args,
            "reminder_minutes",
            "remind_before_minutes",
            "alarm_minutes",
            "reminder",
            "alarm",
        )
        if raw in (None, ""):
            desc = str(raw_args.get("description") or "")
            if re.search(r"\b(remind|reminder|alarm)\b", desc, re.I):
                raw = desc
        if raw in (None, "", False):
            return None
        if raw is True:
            return 10
        if isinstance(raw, (int, float)):
            return max(0, int(raw))
        text = str(raw).strip().lower()
        if text in {"none", "no", "off", "false"}:
            return None
        m = re.search(r"(?<!\d)(\d+)\s*(?:minutes?|mins?|m)\b", text)
        if m:
            return max(0, int(m.group(1)))
        m = re.search(r"(?<!\d)(\d+)\s*(?:hours?|hrs?|h)\b", text)
        if m:
            return max(0, int(m.group(1)) * 60)
        if text.isdigit():
            return max(0, int(text))
        return None

    def _event_description(raw_args, minutes_before: Optional[int]) -> str:
        desc = str(raw_args.get("description", "") or "")
        if minutes_before is None:
            return desc
        reminder_only = re.compile(
            r"^\s*(?:remind(?:er)?|alarm)\s*(?::\s*)?\d+\s*"
            r"(?:minutes?|mins?|m|hours?|hrs?|h)\b.*$",
            re.I,
        )
        return "" if reminder_only.match(desc) else desc

    def _parse_event_dt(raw: str) -> tuple[datetime, bool]:
        """Parse agent event datetimes in the user's timezone when available."""
        if args.get('timezone'):
            return _explicit_calendar_time(raw, args['timezone'])
        return _parse_dt_pair(parse_due_for_user(raw))

    def _parse_all_day_event_dt(raw: str) -> tuple[datetime, bool]:
        """Preserve literal calendar dates for all-day events.

        A date-only all-day value like ``2026-10-24`` is not an instant in UTC;
        it is the user's calendar day. Routing it through parse_due_for_user()
        shifts the stored naive datetime for positive timezones and makes
        birthdays render on the previous date.
        """
        text = str(raw or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return datetime.fromisoformat(text), False
        return _parse_event_dt(text)

    def _looks_like_timed_dt(raw) -> bool:
        text = str(raw or "").strip()
        if not text or re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return False
        return bool(
            re.search(r"\d{4}-\d{2}-\d{2}[T\s]\d{1,2}:\d{2}", text)
            or re.search(r"\b\d{1,2}:\d{2}\b", text)
            or re.search(r"\b\d{1,2}\s*(?:am|pm)\b", text, re.I)
        )

    def _first_nonempty_arg(*names: str):
        for name in names:
            value = args.get(name)
            if value not in (None, ""):
                return value
        return None

    def _create_calendar_reminder(summary: str, location: str, dtstart: datetime,
                                  all_day: bool, minutes_before: int,
                                  is_utc: bool = False) -> tuple[Optional[str], Optional[str]]:
        remind_at = dtstart - timedelta(minutes=minutes_before)
        now = datetime.utcnow() if is_utc else datetime.now()
        if dtstart <= now:
            return None, "event already passed"
        if remind_at <= now:
            # If the requested "before" time already passed but the event is
            # still upcoming, create an immediate Note reminder instead of
            # silently dropping it.
            remind_at = now
        start_fmt = dtstart.strftime("%a %b %d") if all_day else dtstart.strftime("%a %b %d %H:%M")
        loc = f" @ {location}" if location else ""
        text = f"{summary}{loc} — {start_fmt}"
        due_date = remind_at.isoformat() + ("Z" if is_utc else "")
        expected_title = f"Calendar reminder: {summary}"
        existing_q = db.query(Note).filter(
            Note.archived == False,  # noqa: E712
            Note.due_date == due_date,
        )
        if owner is not None:
            existing_q = existing_q.filter(Note.owner == owner)
        target_title = re.sub(r"^\s*(?:calendar\s+)?reminder\s*:\s*", "", expected_title.strip().lower())
        for existing in existing_q.limit(25).all():
            existing_title = re.sub(r"^\s*(?:calendar\s+)?reminder\s*:\s*", "", (existing.title or "").strip().lower())
            if existing_title == target_title:
                return existing.id, "duplicate reminder already exists"
        note = Note(
            id=str(_uuid.uuid4()),
            owner=owner,
            title=expected_title,
            items=json.dumps([{"text": text, "done": False, "checked": False}]),
            note_type="todo",
            label="calendar",
            due_date=due_date,
            source="calendar",
        )
        db.add(note)
        return note.id, None

    try:
        if action == "list_calendars":
            _ensure_default_calendar(db, owner)
            # This read path intentionally persists the lazily-created default;
            # event creation commits it in the event's transaction instead.
            db.commit()
            cals = _calendar_query().all()
            result = [{"name": c.name, "href": c.id} for c in cals]
            if result:
                lines = [f"Found {len(result)} calendar(s):"]
                for c in result:
                    lines.append(f"- {c['name']} ({c['href'][:8]})")
                response_text = "\n".join(lines)
            else:
                response_text = "No calendars found."
            return {"response": response_text, "calendars": result, "exit_code": 0}

        elif action == "list_events":
            try:
                start_raw = _first_nonempty_arg(
                    "start", "start_time", "start_date", "range_start", "from", "dtstart", "since"
                )
                end_raw = _first_nonempty_arg(
                    "end", "end_time", "end_date", "range_end", "to", "dtend", "until"
                )
                query_raw = args.get("query")
                range_query_raw = args.get("date_range") or args.get("range")
                if (query_raw or range_query_raw) and (not start_raw or not end_raw):
                    return {
                        "error": (
                            "list_events needs explicit start/end ISO datetimes; "
                            f"resolve the requested range ({(query_raw or range_query_raw)!r}) and call manage_calendar again."
                        ),
                        "exit_code": 1,
                    }
                if start_raw:
                    start_dt, _ = _parse_event_dt(start_raw)
                else:
                    start_dt = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
                if end_raw:
                    end_dt, _ = _parse_event_dt(end_raw)
                else:
                    end_dt = start_dt + timedelta(days=14)
            except ValueError as e:
                return {"error": f"Invalid date format: {e}", "exit_code": 1}

            if end_dt <= start_dt:
                end_dt = start_dt + timedelta(days=1)

            q = _event_query().filter(
                CalendarEvent.dtstart < end_dt,
                CalendarEvent.dtend > start_dt,
                CalendarEvent.status != "cancelled",
            )
            calendar_filter = args.get("calendar")
            if calendar_filter:
                q = q.filter(
                    (CalendarEvent.calendar_id == calendar_filter) |
                    (CalendarCal.name == calendar_filter)
                )
            rows = q.order_by(CalendarEvent.dtstart).all()
            if query_raw:
                needle = str(query_raw).strip().lower()
                if needle:
                    rows = [
                        ev for ev in rows
                        if needle in (ev.summary or "").lower()
                        or needle in (ev.description or "").lower()
                        or needle in (ev.location or "").lower()
                        or needle in (ev.event_type or "").lower()
                    ]
            events = []
            for ev in rows:
                events.append(_event_to_dict(ev, db=db, owner=owner))
            if not events:
                response_text = f"No events between {start_dt.date().isoformat()} and {end_dt.date().isoformat()}."
            else:
                lines = [f"Found {len(events)} event(s) between {start_dt.date().isoformat()} and {end_dt.date().isoformat()}:"]
                for ev in events:
                    when = ev["dtstart"]
                    when_str = f"{when} (all day)" if ev.get("all_day") else f"{when} -> {ev.get('dtend', '')}"
                    # Clickable anchor — opens the calendar on the event's day.
                    line = f"- {when_str}: [{ev['summary']}](#event-{ev['uid']})"
                    if ev.get("event_type"):
                        line += f" #{ev['event_type']}"
                    if ev.get("importance") and ev["importance"] != "normal":
                        line += f" !{ev['importance']}"
                    if ev.get("rrule"):
                        line += f" repeats({ev['rrule']})"
                    if ev.get("has_reminder"):
                        minutes = ev.get("reminder_minutes")
                        line += f" 🔔 reminder"
                        if minutes is not None:
                            line += f" {minutes} min before"
                    if ev.get("location"):
                        line += f" @ {ev['location']}"
                    if ev.get("calendar"):
                        line += f" ({ev['calendar']})"
                    if ev.get("description"):
                        desc = ev["description"].strip().replace("\n", " ")
                        if len(desc) > 120:
                            desc = desc[:117] + "..."
                        line += f"\n    {desc}"
                    lines.append(line)
                response_text = "\n".join(lines)
            return {"response": response_text, "events": events, "exit_code": 0}

        elif action == "create_event":
            summary = args.get("summary")
            # Accept the various names models like to use for the start
            # field: dtstart (canonical), start, start_time, when.
            dtstart_str = (args.get("dtstart") or args.get("start")
                           or args.get("start_time") or args.get("when"))
            if not summary or not dtstart_str:
                return {"error": "summary and dtstart are required", "exit_code": 1}

            # Accept either an href OR a calendar name/short-id like "Main"
            # or "62e545d8" — saves the model from having to memorize hrefs
            # after a `list_calendars` call returned short prefixes.
            cal_href = args.get("calendar_href") or args.get("calendar")
            cal = None
            if cal_href:
                cal = (_calendar_query()
                       .filter(CalendarCal.id == cal_href)
                       .first())
                if not cal:
                    # Try by name (case-insensitive) or by short-id prefix
                    cal = (_calendar_query()
                           .filter(CalendarCal.name.ilike(cal_href))
                           .first())
                if not cal:
                    cal = (_calendar_query()
                           .filter(CalendarCal.id.like(f"{cal_href}%"))
                           .first())
            if not cal:
                cal = _ensure_default_calendar(db, owner)

            all_day = bool(args.get("all_day", False))
            try:
                dtstart, dtstart_is_utc = (
                    _parse_all_day_event_dt(dtstart_str)
                    if all_day
                    else _parse_event_dt(dtstart_str)
                )
            except ValueError as e:
                return {"error": f"Could not parse dtstart {dtstart_str!r}: {e}", "exit_code": 1}
            dtend_raw = args.get("dtend") or args.get("end") or args.get("end_time")
            if dtend_raw:
                try:
                    dtend, dtend_is_utc = (
                        _parse_all_day_event_dt(dtend_raw)
                        if all_day
                        else _parse_event_dt(dtend_raw)
                    )
                    dtstart_is_utc = dtstart_is_utc or dtend_is_utc
                except ValueError as e:
                    return {"error": f"Could not parse dtend {dtend_raw!r}: {e}", "exit_code": 1}
            else:
                # Support duration: "1h", "30m", "90min", "1hr30m"
                dur = (args.get("duration") or "").strip().lower()
                delta = None
                if dur:
                    import re as _re_d
                    h = _re_d.search(r'(?<!\d)(\d+)\s*(?:h|hr|hours?)', dur)
                    m = _re_d.search(r'(?<!\d)(\d+)\s*(?:m|min|minutes?)', dur)
                    secs = (int(h.group(1)) * 3600 if h else 0) + (int(m.group(1)) * 60 if m else 0)
                    if secs > 0:
                        delta = timedelta(seconds=secs)
                if delta is not None:
                    dtend = dtstart + delta
                elif all_day:
                    dtend = dtstart + timedelta(days=1)
                else:
                    dtend = dtstart + timedelta(hours=1)

            # Dedup: if a non-cancelled event with the same title + start time already
            # exists, return its UID instead of creating a fresh copy. Prevents the
            # email triage from multiplying events when several emails reference the
            # same meeting. Compare case-insensitively since LLM-extracted titles
            # can vary in capitalisation.
            from sqlalchemy import func as _func
            existing = (
                _event_query()
                .filter(
                    *([CalendarEvent.uid == import_event_uid] if import_event_uid else [
                        CalendarEvent.dtstart == dtstart,
                        CalendarEvent.status != "cancelled",
                        _func.lower(CalendarEvent.summary) == summary.lower(),
                    ]),
                )
                .first()
            )
            if existing is not None:
                # Repair older email-imported events whose model-generated
                # location was an unrelated map URL. A concrete meeting URL
                # is stronger evidence than the existing free-text location.
                incoming_location = str(args.get("location") or "").strip()
                changed = False
                if incoming_location and re.match(
                    r"^https?://(?:teams\.microsoft\.com|(?:[a-z0-9-]+\.)?zoom\.us|meet\.google\.com|(?:[a-z0-9-]+\.)?webex\.com|meet\.jit\.si)/",
                    incoming_location,
                    re.IGNORECASE,
                ) and (
                    not str(existing.location or "").strip()
                    or not re.match(
                        r"^https?://(?:teams\.microsoft\.com|(?:[a-z0-9-]+\.)?zoom\.us|meet\.google\.com|(?:[a-z0-9-]+\.)?webex\.com|meet\.jit\.si)/",
                        str(existing.location or "").strip(),
                        re.IGNORECASE,
                    )
                ):
                    existing.location = incoming_location
                    changed = True
                for field in ("source_email_uid", "source_email_folder", "source_email_account_id", "source_email_message_id"):
                    incoming = str(args.get(field) or "").strip()
                    if incoming and not getattr(existing, field, None):
                        setattr(existing, field, incoming)
                        changed = True
                if changed:
                    db.commit()
                reminder_note_id = None
                reminder_skipped_reason = None
                minutes_before = _reminder_minutes(args)
                if minutes_before is not None:
                    reminder_note_id, reminder_skipped_reason = _create_calendar_reminder(
                        existing.summary or summary,
                        existing.location or "",
                        existing.dtstart,
                        existing.all_day,
                        minutes_before,
                        bool(existing.is_utc),
                    )
                    if reminder_note_id:
                        db.commit()
                reminder_text = ""
                if minutes_before is not None:
                    reminder_text = (
                        f"; reminder set {minutes_before} min before"
                        if reminder_note_id
                        else f"; reminder not set ({reminder_skipped_reason or 'reminder time already passed'})"
                    )
                return {
                    "response": (
                        f"Event already exists: [{summary}](#event-{existing.uid}) on {_saved_event_times(existing)['dtstart']}"
                        + reminder_text
                    ),
                    "uid": existing.uid,
                    **_saved_event_times(existing),
                    "anchor": f"[{summary}](#event-{existing.uid})",
                    "has_reminder": bool(reminder_note_id),
                    "reminder_note_id": reminder_note_id,
                    "reminder_minutes": minutes_before if reminder_note_id else None,
                    "reminder_skipped_reason": reminder_skipped_reason,
                    "duplicate": True,
                    "exit_code": 0,
                }

            # Optional tag/category and importance — friendly aliases.
            event_type = (args.get("event_type") or args.get("tag")
                          or args.get("category") or args.get("type") or "") or None
            importance = args.get("importance") or "normal"
            minutes_before = _reminder_minutes(args)

            event_description = _event_description(args, minutes_before)
            event_location = args.get("location", "") or ""
            missing_id = reserve_upload_references(
                get_upload_handler(),
                owner,
                event_description,
                event_location,
            )
            if missing_id:
                return {
                    "error": f"Referenced upload is no longer available: {missing_id}",
                    "exit_code": 1,
                }

            uid = import_event_uid or str(_uuid.uuid4())
            ev = CalendarEvent(
                uid=uid, calendar_id=cal.id, summary=summary,
                description=event_description,
                location=event_location,
                dtstart=dtstart, dtend=dtend, all_day=all_day,
                is_utc=dtstart_is_utc and not all_day,
                rrule=args.get("rrule", "") or "",
                event_type=event_type,
                importance=importance,
                source_email_uid=str(args.get("source_email_uid") or "").strip() or None,
                source_email_folder=str(args.get("source_email_folder") or "").strip() or None,
                source_email_account_id=str(args.get("source_email_account_id") or "").strip() or None,
                source_email_message_id=str(args.get("source_email_message_id") or "").strip() or None,
                caldav_sync_pending="create" if cal.source == "caldav" else None,
            )
            db.add(ev)
            reminder_note_id = None
            reminder_skipped_reason = None
            if minutes_before is not None:
                reminder_note_id, reminder_skipped_reason = _create_calendar_reminder(
                    summary,
                    args.get("location", "") or "",
                    dtstart,
                    all_day,
                    minutes_before,
                    dtstart_is_utc and not all_day,
                )
            db.commit()
            if cal.source == "caldav":
                await _push_caldav_event_after_commit(owner, uid, "create")
            tag_blurb = f" [{event_type}]" if event_type else ""
            if minutes_before is None:
                reminder_blurb = ""
            elif reminder_note_id:
                reminder_blurb = f" with reminder {minutes_before} min before"
            else:
                reminder_blurb = f" without reminder ({reminder_skipped_reason or 'reminder time already passed'})"
            # Return a clickable anchor so the agent can surface a link
            # that opens the calendar on that day. See the markdown
            # anchor convention ([Name](#event-<uid>)).
            return {
                "response": f"Created event [{summary}](#event-{uid}){tag_blurb} on {_saved_event_times(ev)['dtstart']}{reminder_blurb}",
                "uid": uid,
                **_saved_event_times(ev),
                "anchor": f"[{summary}](#event-{uid})",
                "has_reminder": bool(reminder_note_id),
                "reminder_note_id": reminder_note_id,
                "reminder_minutes": minutes_before if reminder_note_id else None,
                "reminder_skipped_reason": reminder_skipped_reason,
                "exit_code": 0,
            }

        elif action == "update_event":
            # Compact routers sometimes call the identifier field ``id`` and
            # place the event title there. Accept both forms, but resolve a
            # title only when it is unique within the owner's calendar.
            uid = args.get("uid") or args.get("id") or args.get("title")
            if not uid and args.get("summary"):
                uid = args.get("summary")
            if not uid:
                return {"error": "uid is required", "exit_code": 1}
            ev = None
            base_uid = ""
            for candidate_uid in _event_uid_candidates(uid):
                try:
                    candidate_base_uid = _resolve_base_uid(candidate_uid)
                except ValueError:
                    continue
                ev = _event_query().filter(CalendarEvent.uid == candidate_base_uid).first()
                if ev:
                    base_uid = candidate_base_uid
                    break
            if not ev:
                title_matches = _event_query().filter(
                    CalendarEvent.summary == str(uid).strip()
                ).all()
                if len(title_matches) == 1:
                    ev = title_matches[0]
                    base_uid = ev.uid
                elif len(title_matches) > 1:
                    return {
                        "error": "Multiple events have that exact title; uid is required",
                        "exit_code": 1,
                    }
            if not ev:
                return {"error": f"Event {uid} not found", "exit_code": 1}
            missing_id = reserve_upload_references(
                get_upload_handler(),
                owner,
                args.get("description"),
                args.get("location"),
            )
            if missing_id:
                return {
                    "error": f"Referenced upload is no longer available: {missing_id}",
                    "exit_code": 1,
                }
            if args.get("summary") is not None:
                ev.summary = args["summary"]
            if args.get("description") is not None:
                ev.description = args["description"]
            if args.get("location") is not None:
                ev.location = args["location"]
            previous_dtstart = ev.dtstart
            previous_dtend = ev.dtend
            if args.get("dtstart") is not None:
                # Anchor naive/natural-language input to the USER's timezone and
                # refresh is_utc, exactly like create_event. Parsing with the
                # raw server-local _parse_dt here (and never touching is_utc)
                # silently shifted an updated event by the user's UTC offset.
                _eff_all_day = (
                    args["all_day"] if args.get("all_day") is not None else ev.all_day
                )
                if args.get("all_day") is None and bool(ev.all_day) and _looks_like_timed_dt(args["dtstart"]):
                    _eff_all_day = False
                    ev.all_day = False
                ev.dtstart, _su = _parse_event_dt(args["dtstart"])
                ev.is_utc = bool(_su and not _eff_all_day)
                if (
                    args.get("dtend") is None
                    and previous_dtstart is not None
                    and previous_dtend is not None
                    and previous_dtend > previous_dtstart
                ):
                    ev.dtend = ev.dtstart + (previous_dtend - previous_dtstart)
            if args.get("dtend") is not None:
                ev.dtend, _eu = _parse_event_dt(args["dtend"])
                if args.get("all_day") is None and bool(ev.all_day) and _looks_like_timed_dt(args["dtend"]):
                    ev.all_day = False
            if args.get("all_day") is not None:
                ev.all_day = args["all_day"]
            # Tag/category + importance updates (any of these aliases).
            _tag = (args.get("event_type") or args.get("tag")
                    or args.get("category") or args.get("type"))
            if _tag is not None:
                ev.event_type = _tag or None
            if args.get("importance") is not None:
                ev.importance = args["importance"]
            for field in ("source_email_uid", "source_email_folder", "source_email_account_id", "source_email_message_id"):
                incoming = str(args.get(field) or "").strip()
                if incoming:
                    setattr(ev, field, incoming)
            if args.get("rrule") is not None:
                ev.rrule = args.get("rrule") or ""
            elif str(args.get("repeat") or "").strip().lower() in {"none", "no", "off", "false", "single"}:
                ev.rrule = ""

            reminder_text = ""
            reminder_note_id = None
            reminder_skipped_reason = None
            minutes_before = None
            if _has_reminder_request(args):
                _delete_calendar_reminders_for_event(db, owner, ev)
                minutes_before = _reminder_minutes(args)
                if minutes_before is None:
                    reminder_text = "; reminder removed"
                else:
                    reminder_note_id, reminder_skipped_reason = _create_calendar_reminder(
                        ev.summary or "",
                        ev.location or "",
                        ev.dtstart,
                        bool(ev.all_day),
                        minutes_before,
                        bool(ev.is_utc),
                    )
                    if reminder_note_id:
                        reminder_text = f"; reminder set {minutes_before} min before"
                    else:
                        reminder_text = f"; reminder not set ({reminder_skipped_reason or 'reminder time already passed'})"

            is_caldav = ev.calendar and ev.calendar.source == "caldav"
            if is_caldav:
                ev.caldav_sync_pending = "update"
            db.commit()
            if is_caldav:
                await _push_caldav_event_after_commit(owner, base_uid, "update")
            return {
                "response": f"Updated event [{ev.summary or uid}](#event-{base_uid}){reminder_text}",
                "uid": base_uid,
                **_saved_event_times(ev),
                "anchor": f"[{ev.summary or uid}](#event-{base_uid})",
                "has_reminder": bool(reminder_note_id) or bool(_calendar_reminder_for_event(db, owner, ev)),
                "reminder_note_id": reminder_note_id,
                "reminder_minutes": minutes_before if reminder_note_id else None,
                "reminder_skipped_reason": reminder_skipped_reason,
                "exit_code": 0,
            }

        elif action == "delete_event":
            uid = args.get("uid")
            if not uid and args.get("summary"):
                # Exact-title deletion is safe when the title is unique and
                # avoids forcing a weak router through an unnecessary list
                # round-trip. Refuse ambiguous matches.
                matches = _event_query().filter(
                    CalendarEvent.summary == str(args.get("summary")).strip()
                ).all()
                if len(matches) == 1:
                    uid = matches[0].uid
                elif len(matches) > 1:
                    return {"error": "Multiple events have that exact title; uid is required", "exit_code": 1}
            if not uid:
                return {"error": "uid or exact summary is required", "exit_code": 1}
            ev = None
            base_uid = ""
            for candidate_uid in _event_uid_candidates(uid):
                try:
                    candidate_base_uid = _resolve_base_uid(candidate_uid)
                except ValueError:
                    continue
                ev = _event_query().filter(CalendarEvent.uid == candidate_base_uid).first()
                if ev:
                    base_uid = candidate_base_uid
                    break
            if not ev:
                return {"error": f"Event {uid} not found", "exit_code": 1}
            is_caldav = ev.calendar and ev.calendar.source == "caldav" and ev.remote_href
            if is_caldav:
                _record_caldav_delete_tombstone(db, ev, owner)
            _delete_calendar_reminders_for_event(db, owner, ev)
            db.delete(ev)
            db.commit()
            if is_caldav:
                await _push_caldav_event_after_commit(owner, base_uid, "delete")
            return {"response": f"Deleted event {uid}", "exit_code": 0}

        else:
            return {
                "error": f"Unknown action: {action}. Use list_events, create_event, update_event, delete_event, list_calendars",
                "exit_code": 1,
            }

    except Exception as e:
        db.rollback()
        logger.error(f"manage_calendar error: {e}")
        return {"error": str(e), "exit_code": 1}
    finally:
        db.close()
