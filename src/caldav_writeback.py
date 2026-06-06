"""CalDAV write-back: push local create/update/delete out to the remote (#800).

``src/caldav_sync.py`` is a one-way pull (remote → local). So events created,
edited, or deleted in Odysseus on a CalDAV-backed calendar only changed the local
SQLite copy and never reached the server (iCloud/Nextcloud/Radicale/Fastmail) —
they'd silently disappear on the next pull and never show on the user's phone.

This adds the missing write half. The remote calendar URL isn't stored locally
(the local calendar id is a one-way hash of it), so we re-discover the remote
calendar by matching that same hash, then PUT/DELETE the VEVENT by its UID via
the `caldav` lib. Writes are best-effort: the local DB stays the source of truth,
and a remote failure is reported, never fatal to the local operation.

The pure pieces (``build_event_ical``, ``find_remote_calendar``, ``push_event``)
take their inputs by argument so they unit-test against a fake client with no
network.
"""

import asyncio
import logging
from datetime import timezone

logger = logging.getLogger(__name__)


def _stable_cal_id(remote_url: str, owner: str = "", account_id: str = "") -> str:
    # Reuse the sync module's hashing so owner+account_id scoping stays consistent.
    from src.caldav_sync import _stable_cal_id as _sync_id
    return _sync_id(remote_url, owner=owner, account_id=account_id)


def build_event_ical(ev: dict) -> str:
    """Serialize a local event dict to a VCALENDAR/VEVENT iCalendar string.

    ``ev`` keys: uid, summary, description, location, dtstart (datetime),
    dtend (datetime), all_day (bool), is_utc (bool), rrule (str).
    Mirrors how the pull path interprets is_utc/all_day so a round-trip is stable.
    """
    from icalendar import Calendar, Event as iEvent
    from icalendar.prop import vRecur

    cal = Calendar()
    cal.add("prodid", "-//Odysseus//CalDAV write-back//EN")
    cal.add("version", "2.0")

    ve = iEvent()
    ve.add("uid", ev["uid"])
    ve.add("summary", ev.get("summary") or "")
    if ev.get("description"):
        ve.add("description", ev["description"])
    if ev.get("location"):
        ve.add("location", ev["location"])

    dtstart = ev["dtstart"]
    dtend = ev["dtend"]
    if ev.get("all_day"):
        ve.add("dtstart", dtstart.date())
        ve.add("dtend", dtend.date())
    elif ev.get("is_utc"):
        # Stored as naive-UTC instants — re-attach UTC so the server gets a Z time.
        ve.add("dtstart", dtstart.replace(tzinfo=timezone.utc))
        ve.add("dtend", dtend.replace(tzinfo=timezone.utc))
    else:
        # Legacy naive-local ("floating") time — emit without a TZ.
        ve.add("dtstart", dtstart)
        ve.add("dtend", dtend)

    if ev.get("rrule"):
        try:
            ve.add("rrule", vRecur.from_ical(ev["rrule"]))
        except Exception:
            logger.debug("CalDAV write-back: skipping unparseable rrule %r", ev.get("rrule"))

    cal.add_component(ve)
    return cal.to_ical().decode("utf-8")


def find_remote_calendar(calendars, local_cal_id: str, owner: str = "", account_id: str = ""):
    """Find the remote calendar whose URL hashes to ``local_cal_id``, or None.

    ``owner`` and ``account_id`` must match what was used when the local calendar
    id was originally computed in ``_sync_blocking`` so the hash round-trips."""
    for cal in calendars:
        try:
            if _stable_cal_id(str(cal.url), owner=owner, account_id=account_id) == local_cal_id:
                return cal
        except Exception:
            continue
    return None


def push_event(calendars, local_cal_id: str, ev: dict, *, delete: bool = False,
               owner: str = "", account_id: str = "") -> dict:
    """Create/update (or delete) ``ev`` on the matching remote calendar.

    Returns ``{"ok": bool, ...}``. ``calendars`` is the discovered caldav
    calendar list (injected so this is unit-testable with fakes).
    ``owner`` and ``account_id`` are forwarded to ``find_remote_calendar``
    so the URL hash round-trips correctly (#2765).
    """
    uid = (ev or {}).get("uid") if isinstance(ev, dict) else None
    if not uid:
        return {"ok": False, "error": "event uid is required"}

    remote = find_remote_calendar(calendars, local_cal_id, owner=owner, account_id=account_id)
    if remote is None:
        return {"ok": False, "error": "remote calendar not found"}

    try:
        existing = remote.event_by_uid(uid)
    except Exception:
        existing = None

    if delete:
        if existing is None:
            return {"ok": True, "note": "already absent on remote"}
        existing.delete()
        return {"ok": True}

    ical = build_event_ical(ev)
    if existing is not None:
        existing.data = ical
        existing.save()
        return {"ok": True, "updated": True}
    remote.save_event(ical)
    return {"ok": True, "created": True}


def _discover_calendars(client):
    """Discover the principal's calendars, falling back to the URL itself —
    same strategy as the pull path."""
    from caldav.lib.error import AuthorizationError, NotFoundError
    try:
        return client.principal().calendars()
    except (AuthorizationError, NotFoundError):
        raise
    except Exception:
        try:
            return [client.calendar(url=str(client.url))]
        except Exception:
            return []


def _writeback_blocking(local_cal_id, ev, delete, url, username, password,
                        owner="", account_id="") -> dict:
    import caldav
    client = caldav.DAVClient(url=url, username=username, password=password)
    calendars = _discover_calendars(client)
    if not calendars:
        return {"ok": False, "error": "no remote calendars discovered"}
    return push_event(calendars, local_cal_id, ev, delete=delete,
                      owner=owner, account_id=account_id)


async def writeback_event(owner: str, calendar_source: str, calendar_id: str,
                          ev: dict, *, delete: bool = False) -> dict:
    """Best-effort push of a local change to the remote CalDAV server.

    No-ops (``{"skipped": ...}``) when the calendar isn't CalDAV-backed or no
    credentials are configured. Never raises — a remote failure is logged and
    returned, the local DB remaining the source of truth.
    """
    if calendar_source != "caldav":
        return {"skipped": "not a caldav calendar"}
    try:
        from src.caldav_sync import _load_caldav_accounts
        from src.secret_storage import decrypt
        from core.database import CalendarCal, SessionLocal

        accounts = _load_caldav_accounts(owner)
        if not accounts:
            return {"skipped": "caldav not configured"}

        # Find which account owns this calendar.
        acc = None
        if len(accounts) > 1:
            db = SessionLocal()
            try:
                cal_row = db.query(CalendarCal).filter(CalendarCal.id == calendar_id).first()
                cal_account_id = cal_row.account_id if cal_row else None
            finally:
                db.close()
            if cal_account_id:
                acc = next((a for a in accounts if a.get("id") == cal_account_id), None)
        # Fall back to first account (covers single-account and legacy rows with
        # no account_id stamped).
        if acc is None:
            acc = accounts[0]

        url = (acc.get("url") or "").strip()
        user = (acc.get("username") or "").strip()
        pw = decrypt(acc.get("password") or "")
        if not (url and user and pw):
            return {"skipped": "caldav account credentials incomplete"}
        from src.caldav_sync import validate_caldav_url
        try:
            url = validate_caldav_url(url)
        except ValueError as e:
            logger.warning("CalDAV write-back URL rejected: %s", e)
            return {"ok": False, "error": str(e)[:200]}
        acc_id = acc.get("id") or ""
        result = await asyncio.to_thread(
            _writeback_blocking, calendar_id, ev, delete, url, user, pw, owner, acc_id
        )
        if not result.get("ok"):
            logger.warning("CalDAV write-back did not apply: %s", result.get("error") or result)
        return result
    except Exception as e:
        logger.exception("CalDAV write-back raised")
        return {"ok": False, "error": str(e)[:200]}
